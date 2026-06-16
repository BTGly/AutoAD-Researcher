"""IdeaGenerator — 读取 idea_context，调用 backend，生成 idea_candidates。

Core 负责 backend 输出的重新校验和一致性验证。
"""

from pathlib import Path

from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.ideation.base import IdeaGenerationBackend
from autoad_researcher.schemas import IdeaContext, IdeaGenerationResult


class IdeaGenerator:
    """调用 Idea backend 生成候选方案。"""

    def __init__(
        self,
        backend: IdeaGenerationBackend,
        runs_root: str | Path = "runs",
    ) -> None:
        self._backend = backend
        self._artifacts = ArtifactStore(runs_root=runs_root)

    def run(self, run_id: str) -> StageResult:
        context = self._artifacts.read_model(
            run_id, "idea_context.json", IdeaContext
        )

        if context.run_id != run_id:
            raise ValueError("idea context run_id mismatch")

        raw_result = self._backend.generate_ideas(context=context)
        result = _revalidate_result(raw_result)
        _validate_result_against_context(result, context, run_id)

        self._artifacts.write_json(run_id, "idea_candidates.json", result)

        return StageResult(
            run_id=run_id,
            stage="idea_generation",
            status="success",
            artifacts=["idea_candidates.json"],
            metadata={
                "backend": self._backend.__class__.__name__,
                "mode": result.mode,
                "candidate_count": len(result.candidates),
                "recommended_count": len(result.recommended_candidate_ids),
            },
        )


# ------------------------------------------------------------------
# Backend 输出校验（纯函数）
# ------------------------------------------------------------------


def _revalidate_result(raw_result) -> IdeaGenerationResult:
    """强制走一遍完整的 Pydantic 校验，防止 model_copy() 绕过。"""
    if isinstance(raw_result, IdeaGenerationResult):
        payload = raw_result.model_dump(mode="json")
    else:
        payload = raw_result
    return IdeaGenerationResult.model_validate(payload)


def _validate_result_against_context(
    result: IdeaGenerationResult,
    context: IdeaContext,
    run_id: str,
) -> None:
    if result.run_id != run_id:
        raise ValueError("idea generation result run_id mismatch")

    if result.mode != context.route.mode:
        raise ValueError("idea generation result mode mismatch")

    allowed_source_ids = set(context.clarified_task.source_ids)

    for candidate in result.candidates:
        for ref in candidate.evidence:
            if ref.source_id is not None and ref.source_id not in allowed_source_ids:
                raise ValueError(
                    "candidate evidence source_id not in clarified source_ids"
                )
