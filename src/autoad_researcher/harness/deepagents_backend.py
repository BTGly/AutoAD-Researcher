"""DeepAgentsHarness — 基于 Deep Agents 的长程 Agent 执行后端。

将 spike 中验证过的 create_deep_agent + FilesystemBackend + FilesystemPermission
封装为 AgentHarness 接口实现。

安全约束：
- FilesystemBackend 不实现 SandboxBackendProtocol，execute 工具自动屏蔽
- FilesystemPermission 将读写限制在 runs/{run_id}/** 内
- 不修改源码，不删除文件
"""

import os
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware import FilesystemPermission

from autoad_researcher.harness.base import AgentHarness

# 复用 spike schema（后续统一迁到 autoad_researcher/schemas/）
import sys

SPIKE_DIR = Path(__file__).resolve().parents[3] / "spikes" / "deepagents_harness"
sys.path.insert(0, str(SPIKE_DIR))
from schema import ExperimentPlan, PatchPlan  # noqa: E402


class DeepAgentsHarness(AgentHarness):
    """基于 Deep Agents 的 harness backend。

    每个方法创建一个受限 Deep Agent：
    - 只能读写 runs/{run_id}/** 内的文件
    - 不能执行 shell
    - 输出必须符合 AutoAD schema
    """

    def __init__(
        self,
        runs_root: str | Path = "runs",
        model: str | None = None,
    ) -> None:
        self._runs_root = Path(runs_root)
        self._model = model or os.getenv("DEEPAGENTS_MODEL", "anthropic:claude-sonnet-4-6")

    def _run_dir(self, run_id: str) -> Path:
        return self._runs_root / run_id

    def _create_agent(self, run_id: str, task_prompt: str):
        """创建针对特定 run_id 的受限 Deep Agent。

        路径白名单：runs/{run_id}/** allow，其他 deny。
        """
        run_allow_path = f"/runs/{run_id}/**"

        return create_deep_agent(
            model=self._model,
            system_prompt=(
                "You are AutoAD-Researcher's DeepAgentsHarness backend. "
                "You must follow filesystem permissions strictly. "
                "You must write only schema-valid JSON artifacts. "
                "Do NOT wrap JSON in markdown. Do NOT add explanatory text to JSON files."
            ),
            backend=FilesystemBackend(
                root_dir=str(Path.cwd()),
                virtual_mode=True,
            ),
            permissions=[
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=[run_allow_path],
                    mode="allow",
                ),
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=["/**"],
                    mode="deny",
                ),
            ],
        )

    def _invoke(self, run_id: str, task_prompt: str) -> None:
        agent = self._create_agent(run_id, task_prompt)
        agent.invoke({"messages": [{"role": "user", "content": task_prompt}]})

    def _validate_output(self, run_dir: Path, filename: str, schema_cls):
        """校验输出的 JSON 是否符合 schema。"""
        import json

        filepath = run_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Agent did not produce: {filepath}")

        data = json.loads(filepath.read_text(encoding="utf-8"))
        schema_cls.model_validate(data)

    # ------------------------------------------------------------------
    # AgentHarness 接口实现
    # ------------------------------------------------------------------

    def run_experiment_planning(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        task = f"""读取 /runs/{run_id}/input_task.yaml 和 /runs/{run_id}/paper_summary.json，
生成实验计划，写入 /runs/{run_id}/experiment_plan.json。

experiment_plan.json 必须包含字段：
- experiment_goal (str)
- baseline (str)
- dataset (str)
- categories (list[str])
- metrics (list[str])
- control_group (str)
- experiment_group (str)
- resource_budget (str)
- risks (list[str])

不允许写入 /runs/{run_id}/ 之外的任何路径。"""

        self._invoke(run_id, task)
        self._validate_output(run_dir, "experiment_plan.json", ExperimentPlan)

    def run_patch_planning(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        task = f"""读取 /runs/{run_id}/input_task.yaml 和 /runs/{run_id}/paper_summary.json，
生成代码修改计划，写入 /runs/{run_id}/patch_plan.json。

patch_plan.json 必须包含字段：
- target_repo (str)
- files_to_inspect (list[str])
- files_to_modify (list[str])
- planned_changes (list[str])
- expected_risks (list[str])
- requires_approval (bool)

不允许写入 /runs/{run_id}/ 之外的任何路径。"""

        self._invoke(run_id, task)
        self._validate_output(run_dir, "patch_plan.json", PatchPlan)
