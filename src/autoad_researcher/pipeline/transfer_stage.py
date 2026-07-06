"""Stage 3.4 transfer_design runner — wires transfer orchestrator into the pipeline."""

import json
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.schemas.approvals import Stage3Approval
from autoad_researcher.schemas.baseline_architecture import BaselineArchitectureContract
from autoad_researcher.schemas.stage3_acceptance import (
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceStageRecord,
)
from autoad_researcher.research_context.freeze import active_freeze_context_path, load_active_freeze_manifest
from autoad_researcher.transfer.orchestrator import (
    StepStatus,
    finalize_transfer_design,
    run_idea_transfer_design,
)


def run_transfer_design_stage(
    run_id: str,
    run_dir: Path,
    stage_dir: Path,
) -> Stage3AcceptanceStageRecord:
    """Run the 3.4 transfer design stage.

    Two-stage approval flow:
    1. idea_confirmation — user selects which idea to transfer
    2. variant_selection — user selects which variant to implement

    Without approval: runs pipeline up to first block point.
    With partial approval: advances to next block point.
    With full approval: produces handoff.
    """
    approvals_dir = run_dir / "approvals"
    handoff_path = stage_dir / "idea_transfer_design_handoff.json"

    # Resume: handoff already exists
    if handoff_path.exists():
        handoff_sha = _sha256_file(handoff_path)
        return Stage3AcceptanceStageRecord(
            stage="transfer_design",
            status="passed",
            handoff_sha256=handoff_sha,
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=str(handoff_path.relative_to(run_dir)),
                    sha256=handoff_sha,
                    artifact_type="idea_transfer_design_handoff",
                ),
            ],
        )

    # Load upstream artifacts
    research_context_path = active_freeze_context_path(run_dir) or run_dir / "context" / "research_context_draft.json"
    if not research_context_path.exists():
        return Stage3AcceptanceStageRecord(
            stage="transfer_design",
            status="blocked",
            blocked_reason="blocked_upstream: research_context_draft.json not found",
        )

    baseline_contract = _load_baseline_contract(run_dir)
    paper_idea_sources = _load_paper_idea_sources(run_dir)

    # Load both approvals
    idea_approval = _load_approval(approvals_dir, "idea_confirmation")
    variant_approval = _load_approval(approvals_dir, "variant_selection")

    idea_confirmed = idea_approval is not None and idea_approval.confirmed_by_user
    variant_selected = variant_approval is not None and variant_approval.confirmed_by_user

    # Build context handoff data, possibly with user_idea_label from approval
    handoff_data = _read_context_as_dict(research_context_path)
    freeze_manifest = load_active_freeze_manifest(run_dir)
    active_freeze_version = freeze_manifest.get("active_freeze_version") if freeze_manifest else None
    handoff_data["origin_research_run_id"] = run_id
    if isinstance(active_freeze_version, str):
        handoff_data["origin_freeze_version"] = active_freeze_version
    if idea_confirmed and idea_approval and idea_approval.user_idea_label:
        handoff_data["user_idea_label"] = idea_approval.user_idea_label

    # Run pipeline to generate variants
    status = run_idea_transfer_design(
        run_id=run_id,
        source_context_id=f"ctx_{run_id}",
        source_context_version=1,
        source_context_sha256=_sha256_file(research_context_path),
        idea_transfer_handoff=handoff_data,
        baseline_contract=baseline_contract,
        paper_idea_sources=paper_idea_sources,
    )

    _write_intermediate_artifacts(stage_dir, status)

    # If blocked and we have idea approval, the user_idea_label may not have
    # matched paper sources.  For internal demo, route as user original idea.
    if status.blocked and idea_confirmed:
        blocked_stage = getattr(status, "stage", "")
        if blocked_stage == "waiting_for_idea_source":
            from autoad_researcher.transfer.router import route_user_original_idea
            idea_source = route_user_original_idea(
                user_description=idea_approval.user_confirmation_text or idea_approval.user_idea_label or "Transfer PatchCore coreset sampling",
                user_evidence_id=f"ev_approval_{run_id}",
            )
            if idea_source.idea_source:
                handoff_data["user_idea_label"] = "confirmed_user_provided"
                # Re-run with the confirmed user-provided source
                status = _run_with_user_provided_idea(
                    run_id=run_id,
                    run_dir=run_dir,
                    handoff_data=handoff_data,
                    idea_source=idea_source.idea_source,
                    baseline_contract=baseline_contract,
                    paper_idea_sources=paper_idea_sources,
                )
                _write_intermediate_artifacts(stage_dir, status)

    # If pipeline blocked and we have variant selection approval → finalize
    if status.blocked and variant_selected and variant_approval is not None:
        blocked_stage = getattr(status, "stage", "")
        if blocked_stage in ("waiting_for_variant_selection",):
            status = finalize_transfer_design(
                status=status,
                run_id=run_id,
                source_context_id=f"ctx_{run_id}",
                source_context_version=1,
                source_context_sha256=_sha256_file(research_context_path),
                baseline_contract=baseline_contract,
                user_evidence_id=f"ev_approval_{run_id}",
                selected_variant_ids=variant_approval.selected_variant_ids,
            )

    # If still blocked, return
    if status.blocked or status.handoff is None:
        return Stage3AcceptanceStageRecord(
            stage="transfer_design",
            status="blocked",
            blocked_reason=f"blocked_variant_selection_required: {status.blocked_reason if status.blocked_reason else 'variants available, no user selection'}",
        )

    # Handoff ready
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(
        json.dumps(status.handoff.model_dump(mode="json", exclude_none=True), indent=2),
        encoding="utf-8",
    )
    handoff_sha = _sha256_file(handoff_path)

    return Stage3AcceptanceStageRecord(
        stage="transfer_design",
        status="passed",
        handoff_sha256=handoff_sha,
        artifacts=[
            Stage3AcceptanceArtifactRef(
                relative_path=str(handoff_path.relative_to(run_dir)),
                sha256=handoff_sha,
                artifact_type="idea_transfer_design_handoff",
            ),
        ],
    )


def _run_with_user_provided_idea(
    run_id: str,
    run_dir: Path,
    handoff_data: dict,
    idea_source,
    baseline_contract: BaselineArchitectureContract | None,
    paper_idea_sources: list[dict],
) -> StepStatus:
    """Re-run transfer pipeline with a user-provided idea source."""
    from autoad_researcher.schemas.transfer_design import IdeaContract
    from autoad_researcher.transfer.normalizer import normalize_idea_contract

    research_context_path = run_dir / "context" / "research_context_draft.json"

    idea = normalize_idea_contract(
        idea_id=f"idea_{run_id}",
        idea_source=idea_source,
        confirmed_by_user=True,
        confirmation_evidence_id=f"ev_confirm_{run_id}",
    )
    handoff_data["confirmed_idea"] = idea.model_dump(mode="json", exclude_none=True)

    return run_idea_transfer_design(
        run_id=run_id,
        source_context_id=f"ctx_{run_id}",
        source_context_version=1,
        source_context_sha256=_sha256_file(research_context_path),
        idea_transfer_handoff=handoff_data,
        baseline_contract=baseline_contract,
        paper_idea_sources=paper_idea_sources,
    )


def _load_baseline_contract(run_dir: Path) -> BaselineArchitectureContract | None:
    path = run_dir / "repository_intelligence" / "baseline_architecture_contract.json"
    if path.exists():
        try:
            return BaselineArchitectureContract.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    alt = run_dir / "baseline_architecture_contract.json"
    if alt.exists():
        try:
            return BaselineArchitectureContract.model_validate_json(alt.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Produce from existing repo artifacts if possible
    return _produce_baseline_contract_from_repo_artifacts(run_dir)


def _produce_baseline_contract_from_repo_artifacts(run_dir: Path) -> BaselineArchitectureContract | None:
    """Produce a baseline contract from repository intelligence artifacts."""
    from autoad_researcher.repository_intelligence.contract_producer import produce_baseline_contract
    from autoad_researcher.schemas.transfer_design import RepositoryReanalysisRequest

    repo_path = run_dir / "workspace" / "local_source"
    repo_summary_path = run_dir / "repository_summary.json"
    entrypoints_path = run_dir / "entrypoints.json"
    modifiable_paths_path = run_dir / "modifiable_paths.json"
    repo_source_path = run_dir / "repository_source.json"

    if not repo_summary_path.exists() and not entrypoints_path.exists():
        return None

    repo_source = {}
    if repo_source_path.exists():
        try:
            repo_source = json.loads(repo_source_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    request = RepositoryReanalysisRequest(
        request_id=f"auto_{run_dir.name}",
        run_id=run_dir.name,
        reason="auto_production_for_transfer_design",
        target_hooks=[],
    )

    repo_summary = None
    if repo_summary_path.exists():
        try:
            repo_summary = json.loads(repo_summary_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    entrypoints = None
    if entrypoints_path.exists():
        try:
            entrypoints = json.loads(entrypoints_path.read_text(encoding="utf-8"))
            if isinstance(entrypoints, dict):
                entrypoints = [entrypoints]
        except Exception:
            pass

    modifiable_paths = None
    if modifiable_paths_path.exists():
        try:
            modifiable_paths = json.loads(modifiable_paths_path.read_text(encoding="utf-8"))
            if isinstance(modifiable_paths, dict):
                modifiable_paths = [modifiable_paths]
        except Exception:
            pass

    try:
        contract = produce_baseline_contract(
            request=request,
            repository_source_id=repo_source.get("source_id", "source_local"),
            repository_commit=repo_source.get("resolved_commit", "unknown"),
            repository_summary=repo_summary,
            entrypoints=entrypoints,
            modifiable_paths=modifiable_paths,
        )
        path = run_dir / "baseline_architecture_contract.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(contract.model_dump(mode="json", exclude_none=True), indent=2),
            encoding="utf-8",
        )
        return contract
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return None


def _load_paper_idea_sources(run_dir: Path) -> list[dict]:
    path = run_dir / "paper" / "artifacts" / "paper_idea_sources.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return [data]
        except Exception:
            pass
    return []


def _load_approval(approvals_dir: Path, decision_type: str) -> Stage3Approval | None:
    path = approvals_dir / f"{decision_type}.json"
    if not path.exists():
        return None
    try:
        return Stage3Approval.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_context_as_dict(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"run_id": str(path.parent.name)}


def _write_intermediate_artifacts(stage_dir: Path, status: StepStatus) -> None:
    if status.idea_contract:
        (stage_dir / "idea_contract.json").write_text(
            json.dumps(status.idea_contract.model_dump(mode="json", exclude_none=True), indent=2),
            encoding="utf-8",
        )
    if status.variants:
        (stage_dir / "implementation_variants.json").write_text(
            json.dumps([v.model_dump(mode="json", exclude_none=True) for v in status.variants], indent=2),
            encoding="utf-8",
        )
    if status.selection:
        (stage_dir / "variant_selection.json").write_text(
            json.dumps(status.selection.model_dump(mode="json", exclude_none=True), indent=2),
            encoding="utf-8",
        )
    if status.analysis:
        (stage_dir / "transfer_analysis.json").write_text(
            json.dumps(status.analysis.model_dump(mode="json", exclude_none=True), indent=2),
            encoding="utf-8",
        )


def _sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
