"""SimplePipelineHarness — 无 Agent 依赖的确定性 harness。

用于 mock、测试、离线运行和稳定闭环。
不调用任何 LLM，直接从输入生成符合 schema 的占位输出。
"""

from pathlib import Path

from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.harness.base import AgentHarness
from autoad_researcher.schemas import ExperimentPlan, PatchPlan


class SimplePipelineHarness(AgentHarness):
    """确定性 harness，不依赖 LLM。

    读取 runs/{run_id}/ 中的输入文件，生成符合 schema 的占位输出。
    用于保证"不依赖 Deep Agents 也能跑通闭环"的底线能力。
    """

    def __init__(self, runs_root: str | Path = "runs") -> None:
        super().__init__(runs_root=runs_root)
        # 延迟导入避免 core ↔ harness 循环依赖
        from autoad_researcher.core import ArtifactStore, EventStore

        self._artifacts = ArtifactStore(runs_root=runs_root)
        self._events = EventStore(runs_root=runs_root)

    def run_experiment_planning(self, run_id: str) -> StageResult:
        self._run_dir(run_id)

        stage = "experiment_planning"
        backend = "simple_pipeline"
        artifact = "experiment_plan.json"

        self._events.record_stage_started(run_id, stage, backend=backend)

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

        self._artifacts.write_json(run_id, artifact, plan, overwrite=True)

        result = StageResult(
            run_id=run_id,
            stage=stage,
            status="success",
            artifacts=[artifact],
            metadata={"backend": backend},
        )

        self._events.record_stage_completed(
            run_id,
            stage,
            backend=backend,
            artifacts=result.artifacts,
            status=result.status,
        )

        return result

    def run_patch_planning(self, run_id: str) -> StageResult:
        self._run_dir(run_id)

        stage = "patch_planning"
        backend = "simple_pipeline"
        artifact = "patch_plan.json"

        self._events.record_stage_started(run_id, stage, backend=backend)

        patch = PatchPlan(
            target_repo="patchcore-inspection",
            files_to_inspect=["patchcore/patchcore.py", "patchcore/backbones.py"],
            files_to_modify=["patchcore/backbones.py"],
            planned_changes=["新增多尺度特征融合 backbone 类"],
            expected_risks=["SimplePipelineHarness 占位输出，未经过真实推理"],
            requires_approval=True,
        )

        self._artifacts.write_json(run_id, artifact, patch, overwrite=True)

        result = StageResult(
            run_id=run_id,
            stage=stage,
            status="success",
            artifacts=[artifact],
            metadata={"backend": backend},
        )

        self._events.record_stage_completed(
            run_id,
            stage,
            backend=backend,
            artifacts=result.artifacts,
            status=result.status,
        )

        return result
