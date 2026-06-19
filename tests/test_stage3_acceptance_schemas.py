"""Tests for Step 3.10 acceptance schemas."""

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas.stage3_acceptance import (
    STAGE3_ACCEPTANCE_STAGE_ORDER,
    ArtifactChainBinding,
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceManifest,
    Stage3AcceptanceResult,
    Stage3AcceptanceStageRecord,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _artifact(stage: str) -> Stage3AcceptanceArtifactRef:
    return Stage3AcceptanceArtifactRef(
        relative_path=f"stage3_acceptance/stages/{stage}.json",
        sha256=SHA_A,
        artifact_type="stage_acceptance_marker",
    )


def _records() -> list[Stage3AcceptanceStageRecord]:
    return [
        Stage3AcceptanceStageRecord(
            stage=stage,
            status="passed",
            handoff_sha256=SHA_A,
            artifacts=[_artifact(stage)],
        )
        for stage in STAGE3_ACCEPTANCE_STAGE_ORDER
    ]


def test_passed_manifest_requires_complete_stage_order():
    manifest = Stage3AcceptanceManifest(
        run_id="run_310",
        mode="l1-l2",
        stages=_records(),
        final_handoff_sha256=SHA_A,
        sha_chain_closed=True,
        all_stages_completed=True,
    )

    assert [record.stage for record in manifest.stages] == list(STAGE3_ACCEPTANCE_STAGE_ORDER)


def test_duplicate_stage_rejected():
    records = _records()
    records[1] = records[0].model_copy()

    with pytest.raises(ValueError, match="canonical Stage 3 order"):
        Stage3AcceptanceManifest(
            run_id="run_310",
            mode="l1-l2",
            stages=records,
            final_handoff_sha256=SHA_A,
            sha_chain_closed=True,
            all_stages_completed=True,
        )


def test_artifact_chain_binding_rejects_inconsistent_match_flag():
    with pytest.raises(ValueError, match="match must equal SHA equality"):
        ArtifactChainBinding(
            upstream_stage="intake",
            downstream_stage="repository_intelligence",
            upstream_handoff_sha256=SHA_A,
            downstream_input_ref_sha256=SHA_B,
            match=True,
        )


def test_unknown_stage_name_rejected():
    with pytest.raises(ValidationError):
        Stage3AcceptanceStageRecord(
            stage="unknown_stage",
            status="passed",
            handoff_sha256=SHA_A,
            artifacts=[_artifact("unknown_stage")],
        )


def test_successful_result_cannot_include_failed_stage():
    with pytest.raises(ValueError, match="passed result must not include failed_stage"):
        Stage3AcceptanceResult(
            run_id="run_310",
            mode="l1-l2",
            status="passed",
            artifact_dir="runs/run_310/stage3_acceptance",
            artifacts={},
            failed_stage="intake",
        )
