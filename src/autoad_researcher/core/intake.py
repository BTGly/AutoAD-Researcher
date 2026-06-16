"""InputIntake — 持久化一个 run 的原始任务和输入材料清单。

InputIntake 是独立 Core service，当前不接入 PipelineController。
未来由 Controller 编排 input_intake stage 时再统⼀加上 stage 生命周期事件。
"""

from pathlib import Path

from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.schemas import InputTask, SourceManifest


class InputIntake:
    """持久化一个 run 的原始任务和输入材料清单。"""

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._artifacts = ArtifactStore(runs_root=runs_root)

    def persist(
        self,
        run_id: str,
        *,
        task: InputTask,
        manifest: SourceManifest,
    ) -> StageResult:
        """写入 input_task.yaml 和 source_manifest.json。

        所有校验在写文件之前完成，避免部分写入。
        """
        # --- 校验 ---
        self._artifacts.run_dir(run_id)  # triggers run_id validation

        if task.run_id != run_id:
            raise ValueError(
                f"task run_id ({task.run_id!r}) does not match "
                f"requested run_id ({run_id!r})"
            )

        if manifest.run_id != run_id:
            raise ValueError(
                f"manifest run_id ({manifest.run_id!r}) does not match "
                f"requested run_id ({run_id!r})"
            )

        manifest_source_ids = {s.source_id for s in manifest.sources}
        unknown_source_ids = set(task.source_ids) - manifest_source_ids
        if unknown_source_ids:
            raise ValueError(
                f"input task references unknown sources: "
                f"{sorted(unknown_source_ids)}"
            )

        # --- 写入 ---
        self._artifacts.write_json(
            run_id,
            "source_manifest.json",
            manifest,
        )

        self._artifacts.write_yaml(
            run_id,
            "input_task.yaml",
            task,
        )

        return StageResult(
            run_id=run_id,
            stage="input_intake",
            status="success",
            artifacts=["source_manifest.json", "input_task.yaml"],
            metadata={
                "source_count": len(manifest.sources),
                "backend": "autoad_core",
            },
        )
