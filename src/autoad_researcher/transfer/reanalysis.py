"""C21: Reanalysis request builders + SpawnChildRunRequest."""

from autoad_researcher.schemas.transfer_design import (
    PaperReanalysisRequest,
    RepositoryReanalysisRequest,
    SpawnChildRunRequest,
)


def build_repository_reanalysis(
    run_id: str,
    reason: str,
    missing_artifacts: list[str] | None = None,
    target_hooks: list[str] | None = None,
    current_sha: str | None = None,
) -> RepositoryReanalysisRequest:
    """Build a RepositoryReanalysisRequest."""
    import uuid

    return RepositoryReanalysisRequest(
        request_id=f"repo_reanalysis_{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        reason=reason,
        missing_artifacts=missing_artifacts or [],
        target_hooks=target_hooks or [],
        current_contract_sha256=current_sha,
        completion_conditions=["All missing artifacts populated with RepositoryEvidenceRef"],
    )


def build_paper_reanalysis(
    run_id: str,
    reason: str,
    target_method_ids: list[str] | None = None,
) -> PaperReanalysisRequest:
    """Build a PaperReanalysisRequest."""
    import uuid

    return PaperReanalysisRequest(
        request_id=f"paper_reanalysis_{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        reason=reason,
        target_method_ids=target_method_ids or [],
        completion_conditions=["PaperEvidenceRef populated for all target methods"],
    )


def build_spawn_child_run(
    parent_run_id: str,
    reason: str,
    new_idea_label: str | None = None,
) -> SpawnChildRunRequest:
    """Build a SpawnChildRunRequest."""
    import uuid

    valid_reasons = {"parent_idea_non_viable", "user_wants_alternative_idea"}
    if reason not in valid_reasons:
        raise ValueError(f"reason must be one of {valid_reasons}")

    return SpawnChildRunRequest(
        request_id=f"spawn_child_{uuid.uuid4().hex[:12]}",
        parent_run_id=parent_run_id,
        reason=reason,  # type: ignore[arg-type]
        new_idea_label=new_idea_label,
    )
