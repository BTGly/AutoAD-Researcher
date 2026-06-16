"""ArtifactStore — AutoAD run artifact 读写入口。

统一管理 runs/{run_id}/ 下的结构化产物读写，
包括路径安全校验、JSON/YAML 序列化与 Pydantic schema 校验。
"""

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING, TypeVar

import yaml
from pydantic import BaseModel

from autoad_researcher.core.run_id import run_dir_path

if TYPE_CHECKING:
    from autoad_researcher.core.events import EventStore

T = TypeVar("T", bound=BaseModel)

# JSON artifact 文件名白名单。write_json() 只接受这些。
_ALLOWED_JSON_ARTIFACTS = {
    "paper_summary.json",
    "experiment_plan.json",
    "patch_plan.json",
    "source_manifest.json",
}

# YAML artifact 文件名白名单。write_yaml() 只接受这些。
_ALLOWED_YAML_ARTIFACTS = {
    "input_task.yaml",
}


class ArtifactStore:
    """管理 runs/{run_id}/ 下的结构化产物。

    当前职责：
    - run_id 安全校验（委托 core/run_id.py）
    - artifact filename 白名单（JSON / YAML 分离）
    - 统一 JSON 与 YAML 写入与读取
    - Pydantic schema 校验
    - 自动记录 artifact_written / artifact_read 事件
    """

    def __init__(
        self,
        runs_root: str | Path = "runs",
        *,
        enable_events: bool = True,
    ) -> None:
        self._runs_root = Path(runs_root)
        self._events: EventStore | None = None
        if enable_events:
            from autoad_researcher.core.events import EventStore

            self._events = EventStore(runs_root=self._runs_root)

    # ------------------------------------------------------------------
    # 路径 API
    # ------------------------------------------------------------------

    def run_dir(self, run_id: str) -> Path:
        """返回 runs/{run_id}/ 目录（已校验 run_id 安全性）。"""
        return run_dir_path(self._runs_root, run_id)

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
    # JSON 写 / 读
    # ------------------------------------------------------------------

    def write_json(
        self,
        run_id: str,
        filename: str,
        data: BaseModel | dict[str, Any],
        *,
        overwrite: bool = True,
    ) -> Path:
        """写 JSON artifact。filename 必须在 JSON 白名单中。"""
        self._validate_json(filename)

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

        self._record_artifact_written(run_id, filename, overwrite=overwrite)
        return path

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

        self._record_artifact_read(run_id, filename)
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
    # YAML 写 / 读
    # ------------------------------------------------------------------

    def write_yaml(
        self,
        run_id: str,
        filename: str,
        data: BaseModel | dict[str, Any],
        *,
        overwrite: bool = True,
    ) -> Path:
        """写 YAML artifact。filename 必须在 YAML 白名单中。"""
        self._validate_yaml(filename)

        path = self.artifact_path(run_id, filename)

        if path.exists() and not overwrite:
            raise FileExistsError(f"artifact already exists: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, BaseModel):
            payload = data.model_dump(mode="json", exclude_none=True)
        else:
            payload = data

        path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        self._record_artifact_written(run_id, filename, overwrite=overwrite)
        return path

    def read_yaml(
        self,
        run_id: str,
        filename: str,
    ) -> dict[str, Any]:
        """读取 YAML artifact，返回 dict。"""
        path = self.artifact_path(run_id, filename)

        if not path.exists():
            raise FileNotFoundError(f"artifact not found: {path}")

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"artifact must be a YAML mapping: {path}")

        self._record_artifact_read(run_id, filename)
        return data

    def read_yaml_model(
        self,
        run_id: str,
        filename: str,
        model_cls: type[T],
    ) -> T:
        """读取 YAML 并用 Pydantic model 校验。"""
        data = self.read_yaml(run_id, filename)
        return model_cls.model_validate(data)

    # ------------------------------------------------------------------
    # 内部校验
    # ------------------------------------------------------------------

    def _validate_artifact_filename(self, filename: str) -> None:
        """联合白名单校验（用于 artifact_path 路径安全）。"""
        if filename not in _ALLOWED_JSON_ARTIFACTS and filename not in _ALLOWED_YAML_ARTIFACTS:
            raise ValueError(f"unsupported artifact filename: {filename!r}")

    def _validate_json(self, filename: str) -> None:
        if filename not in _ALLOWED_JSON_ARTIFACTS:
            raise ValueError(
                f"write_json requires a JSON artifact, got: {filename!r}"
            )

    def _validate_yaml(self, filename: str) -> None:
        if filename not in _ALLOWED_YAML_ARTIFACTS:
            raise ValueError(
                f"write_yaml requires a YAML artifact, got: {filename!r}"
            )

    # ------------------------------------------------------------------
    # 事件记录
    # ------------------------------------------------------------------

    def _record_artifact_written(
        self,
        run_id: str,
        filename: str,
        *,
        overwrite: bool,
    ) -> None:
        if self._events is None:
            return
        self._events.record_artifact_written(
            run_id, filename, overwrite=overwrite
        )

    def _record_artifact_read(self, run_id: str, filename: str) -> None:
        if self._events is None:
            return
        self._events.record_artifact_read(run_id, filename)
