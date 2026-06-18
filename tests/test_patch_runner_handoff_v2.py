"""Tests for PatchRunnerHandoff v2 (multi-workspace) and ArtifactReferenceV2."""

import pytest

from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.schemas.patch_planning import (
    BaselineWorkspaceRef,
    PatchRunnerHandoff,
    VariantWorkspaceHandoff,
)

_SHA = "a" * 64
_HEX40 = "0123456789abcdef0123456789abcdef01234567"
_HEX64_1 = "0123456789abcdef" * 4
_HEX64_2 = "fedcba9876543210" * 4


def _ref(artifact_id="art", artifact_type="manifest", sha=_SHA):
    return ArtifactReferenceV2(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        locator=f"runs/run_test/artifacts/{artifact_id}.json",
        sha256=sha,
    )


def _baseline():
    return BaselineWorkspaceRef(
        workspace_id="ws_base",
        repository_fingerprint="f" * 64,
        repository_commit=_HEX40,
        repository_validation_ref=_ref("val_base", "validation_report"),
    )


def _variant(workspace_id="ws_v1", variant_ids=None):
    return VariantWorkspaceHandoff(
        workspace_id=workspace_id,
        variant_ids=variant_ids or ["v1"],
        repository_fingerprint="0" * 64,
        patch_diff_sha256="1" * 64,
        local_validation_report_sha256="2" * 64,
        patch_application_manifest_ref=_ref("man_" + workspace_id, "manifest"),
        post_patch_validation_report_ref=_ref("pval_" + workspace_id, "validation_report"),
    )


def _handoff(selected=None, workspaces=None):
    return PatchRunnerHandoff(
        run_id="run_test",
        repository_before_commit=_HEX40,
        approved_patch_plan_sha256=_HEX64_1,
        selected_variant_ids=selected if selected is not None else ["v1"],
        experiment_bundle_ref="bundle_001",
        baseline_workspace_ref=_baseline(),
        variant_workspaces=workspaces if workspaces is not None else [_variant()],
    )


# --- ArtifactReferenceV2 --------------------------------------------------


class TestArtifactReferenceV2:
    def test_minimal_valid(self):
        ref = ArtifactReferenceV2(
            artifact_id="a1", artifact_type="metrics_report",
            locator="runs/r/artifacts/a1.json", sha256=_SHA,
        )
        assert ref.sha256 == _SHA

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ArtifactReferenceV2(
                artifact_id="a1", artifact_type="metrics_report",
                locator="x", sha256=_SHA, unexpected="bad",
            )

    def test_invalid_sha256(self):
        with pytest.raises(Exception):
            ArtifactReferenceV2(
                artifact_id="a1", artifact_type="metrics_report",
                locator="x", sha256="not-a-sha",
            )


# --- PatchRunnerHandoff v2 ------------------------------------------------


class TestPatchRunnerHandoffV2:
    def test_valid_single_variant(self):
        h = _handoff()
        assert h.schema_version == 2
        assert h.selected_variant_ids == ["v1"]

    def test_valid_multi_workspace(self):
        h = _handoff(
            selected=["v1", "v2", "v3"],
            workspaces=[
                _variant("ws_v1", ["v1"]),
                _variant("ws_v2", ["v2", "v3"]),
            ],
        )
        assert len(h.variant_workspaces) == 2

    def test_duplicate_selected_variant_ids(self):
        with pytest.raises(Exception, match="duplicate selected_variant_ids"):
            _handoff(
                selected=["v1", "v1"],
                workspaces=[
                    _variant("ws_v1", ["v1"]),
                ],
            )

    def test_duplicate_workspace_id(self):
        with pytest.raises(Exception, match="duplicate workspace_id"):
            _handoff(
                selected=["v1", "v2"],
                workspaces=[
                    _variant("ws_v1", ["v1"]),
                    _variant("ws_v1", ["v2"]),
                ],
            )

    def test_variant_in_multiple_workspaces(self):
        with pytest.raises(Exception, match="variant appears in multiple workspaces"):
            _handoff(
                selected=["v1", "v2"],
                workspaces=[
                    _variant("ws_v1", ["v1", "v2"]),
                    _variant("ws_v2", ["v2"]),
                ],
            )

    def test_selected_not_in_workspace(self):
        with pytest.raises(Exception, match="selected variants not in any workspace"):
            _handoff(
                selected=["v1", "vX"],
                workspaces=[_variant("ws_v1", ["v1"])],
            )

    def test_workspace_variant_not_selected(self):
        with pytest.raises(Exception, match="workspace variants not selected"):
            _handoff(
                selected=["v1"],
                workspaces=[
                    _variant("ws_v1", ["v1"]),
                    _variant("ws_v2", ["v2"]),
                ],
            )

    def test_empty_selected_with_empty_workspaces(self):
        h = _handoff(selected=[], workspaces=[])
        assert h.selected_variant_ids == []

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            PatchRunnerHandoff(
                run_id="run_test",
                repository_before_commit=_HEX40,
                approved_patch_plan_sha256=_HEX64_1,
                selected_variant_ids=["v1"],
                experiment_bundle_ref="bundle_001",
                baseline_workspace_ref=_baseline(),
                variant_workspaces=[_variant()],
                unexpected="bad",
            )

    def test_baseline_ref_uses_v2(self):
        h = _handoff()
        assert isinstance(h.baseline_workspace_ref.repository_validation_ref, ArtifactReferenceV2)
        assert h.baseline_workspace_ref.repository_validation_ref.sha256 == _SHA

    def test_variant_ref_uses_v2(self):
        h = _handoff()
        ws = h.variant_workspaces[0]
        assert isinstance(ws.patch_application_manifest_ref, ArtifactReferenceV2)
        assert isinstance(ws.post_patch_validation_report_ref, ArtifactReferenceV2)
