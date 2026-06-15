"""SimplePipelineHarness — 无 Agent 依赖的确定性 harness。

用于 mock、测试、离线运行和稳定闭环。
不调用任何 LLM，直接从输入生成符合 schema 的占位输出。
"""

import sys
from pathlib import Path

from autoad_researcher.harness.base import AgentHarness

# 复用 spike 里已验证过的 schema（后续移到 autoad_researcher/schemas/）
SPIKE_DIR = Path(__file__).resolve().parents[3] / "spikes" / "deepagents_harness"
sys.path.insert(0, str(SPIKE_DIR))
from schema import ExperimentPlan, PatchPlan  # noqa: E402


class SimplePipelineHarness(AgentHarness):
    """确定性 harness，不依赖 LLM。

    读取 runs/{run_id}/ 中的输入文件，生成符合 schema 的占位输出。
    用于保证"不依赖 Deep Agents 也能跑通闭环"的底线能力。
    """

    # __init__ 和 _run_dir / _validate_run_id 继承自 AgentHarness 基类

    def run_experiment_planning(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        plan = ExperimentPlan(
            experiment_goal="[SimplePipelineHarness] 验证实验计划生成流程",
            baseline="PatchCore",
            dataset="MVTec AD",
            categories=["bottle"],
            metrics=["image-level AUROC", "pixel-level AUROC"],
            control_group="PatchCore 原始配置",
            experiment_group="PatchCore + 待迁移模块",
            resource_budget="单卡 GPU，smoke test",
            risks=["SimplePipelineHarness 占位输出，未经过真实推理"],
        )

        output_path = run_dir / "experiment_plan.json"
        output_path.write_text(
            plan.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )

    def run_patch_planning(self, run_id: str) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        patch = PatchPlan(
            target_repo="patchcore-inspection",
            files_to_inspect=["patchcore/patchcore.py", "patchcore/backbones.py"],
            files_to_modify=["patchcore/backbones.py"],
            planned_changes=["新增多尺度特征融合 backbone 类"],
            expected_risks=["SimplePipelineHarness 占位输出，未经过真实推理"],
            requires_approval=True,
        )

        output_path = run_dir / "patch_plan.json"
        output_path.write_text(
            patch.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
