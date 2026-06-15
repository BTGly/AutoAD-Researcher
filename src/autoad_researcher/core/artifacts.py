"""ArtifactStore — AutoAD run artifact 读写入口。

统一管理 runs/{run_id}/ 下的结构化产物读写，
包括路径安全校验、JSON 序列化与 Pydantic schema 校验。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from autoad_researcher.harness.base import AgentHarness

T = TypeVar("T", bound=BaseModel)

# 当前阶段支持的 artifact 文件名白名单
ALLOWED_ARTIFACTS = {
    "input_task.yaml",
    "paper_summary.json",
    "experiment_plan.json",
    "patch_plan.json",
}


class ArtifactStore:
    """管理 runs/{run_id}/ 下的结构化产物。

    当前职责：
    - 复用 AgentHarness 的 run_id 安全校验
    - 防止 artifact filename 路径穿越
    - 统一 JSON 写入与读取
    - 支持 Pydantic schema 校验
    """

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._runs_root = Path(runs_root)
        self._validator = _RunIdValidator(runs_root=self._runs_root)

    # ------------------------------------------------------------------
    # 路径 API
    # ------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        """返回 runs/{run_id}/ 目录（已校验 run_id 安全性）。"""
        return self._validator.run_dir(run_id)

    def artifact_path(self, run_id: str, filename: str) -> Path:
        """返回 artifact 的完整路径，校验 filename 和路径安全性。"""
        self._validate_artifact_filename(filename)

        run_dir = self.run_dir(run_id)
        path = run_dir / filename

        resolved_run_dir = run_dir.resolve()
        resolved_path = path.resolve()

        try:
            resolved_path.relative_to(resolved_run_dir)
        except ValueError:
            raise ValueError(
                f"artifact path escapes run_dir: "
                f"run_dir={resolved_run_dir}, path={resolved_path}"
            ) from None

        return path

    def exists(self, run_id: str, filename: str) -> bool:
        return self.artifact_path(run_id, filename).exists()

    # ------------------------------------------------------------------
    # 写 API
    # ------------------------------------------------------------------

    def write_json(
        self,
        run_id: str,
        filename: str,
        data: BaseModel | dict[str, Any],
        *,
        overwrite: bool = True,
    ) -> Path:
        """写 JSON artifact。

        Args:
            run_id: run 标识
            filename: artifact 文件名（必须在 ALLOWED_ARTIFACTS 中）
            data: Pydantic model 或 dict
            overwrite: False 时如果文件已存在则抛 FileExistsError
        """
        path = self.artifact_path(run_id, filename)

        if path.exists() and not overwrite:
            raise FileExistsError(f"artifact already exists: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, BaseModel):
            payload = data.model_dump(mode="json", exclude_none=True)
        else:
            payload = data

        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # 读 API
    # ------------------------------------------------------------------

    def read_json(
        self,
        run_id: str,
        filename: str,
    ) -> dict[str, Any]:
        """读取 JSON artifact，返回 dict。"""
        path = self.artifact_path(run_id, filename)

        if not path.exists():
            raise FileNotFoundError(f"artifact not found: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"artifact must be a JSON object: {path}")
        return data

    def read_model(
        self,
        run_id: str,
        filename: str,
        model_cls: type[T],
    ) -> T:
        """读取 JSON 并用 Pydantic model 校验。"""
        data = self.read_json(run_id, filename)
        return model_cls.model_validate(data)

    # ------------------------------------------------------------------
    # 内部校验
    # ------------------------------------------------------------------

    def _validate_artifact_filename(self, filename: str) -> None:
        """只允许白名单 artifact 文件名，防止路径穿越。"""
        if filename not in ALLOWED_ARTIFACTS:
            raise ValueError(f"unsupported artifact filename: {filename!r}")


class _RunIdValidator(AgentHarness):
    """复用 AgentHarness 的 run_id 校验能力。

    ArtifactStore 不需要执行 harness 任务，
    所以这里空实现抽象方法，仅暴露 run_dir() 供外部使用。
    """

    def run_experiment_planning(self, run_id: str) -> None:
        raise NotImplementedError

    def run_patch_planning(self, run_id: str) -> None:
        raise NotImplementedError

    def run_dir(self, run_id: str) -> Path:
        return self._run_dir(run_id)
