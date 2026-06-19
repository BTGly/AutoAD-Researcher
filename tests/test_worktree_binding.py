"""Tests for WorkspaceChangeBinding: clone_shared_changes, build_workspace_binding, merge_workspace_manifests."""

import hashlib

import pytest

from autoad_researcher.code_agent.worktree_manager import (
    build_workspace_binding, clone_shared_changes, merge_workspace_manifests,
)
from autoad_researcher.schemas.patch_planning import (
    PatchPayload, PatchPayloadManifest, PlannedRepositoryChange,
    RepositoryChangePlan, VariantWorkspacePlan,
    compute_canonical_plan_sha256,
    canonical_sha,
)


def _manifest(mid, ws, payloads, sha="c" * 64):
    m = PatchPayloadManifest(
        manifest_id=mid, run_id="run", workspace_id=ws,
        patch_plan_sha256=sha, payloads=payloads,
        proposed_diff_artifact_id="diff_1", proposed_diff_sha256=sha,
        manifest_sha256="0" * 64,
    )
    m.manifest_sha256 = canonical_sha(m)
    return m


def _change(cid, ws="ws", path="src/x.py", kind="create", policy="must_not_exist"):
    return PlannedRepositoryChange(
        change_id=cid, workspace_id=ws, operation_kind=kind,
        target_mode="new_target",
        proposed_symbol=cid.lower() if kind == "create" else None,
        hook_id=None,
        repository_path=path, variant_ids=["v1"], rationale="test",
        target_collision_policy=policy,
        payload_id=f"pld_{cid}",
    )


def _plan(changes=None, **kw):
    changes = changes or [_change("chg_1")]
    ws = kw.get("ws", "ws")
    wp = VariantWorkspacePlan(
        workspace_id=ws, variant_ids=["v1"],
        isolation_mode="shared_workspace",
        base_repository_source_id="src_test", base_commit="a" * 40,
        planned_change_ids=[c.change_id for c in changes],
    )
    plan = RepositoryChangePlan(
        schema_version=2, run_id="run_test",
        patch_plan_id="plan_test",
        repository_source_id="src_test", repository_commit="a" * 40,
        repository_fingerprint="a" * 16,
        idea_id="idea_test",
        changes=changes, workspace_plans=[wp],
        patch_plan_sha256="c" * 64,
    )
    plan = plan.model_copy(update={
        "patch_plan_sha256": compute_canonical_plan_sha256(plan),
    })
    return plan


# ── clone_shared_changes ─────────────────────────────────────────────

class TestCloneSharedChanges:
    def test_clones_with_new_ids(self):
        c = _change("shared_cfg", ws="ws_a", path="cfg.yaml")
        plan = _plan(changes=[c], ws="ws_a")
        clones = clone_shared_changes(plan=plan, target_workspace_id="ws_b")
        assert len(clones) == 1
        clone = clones[0]
        assert clone.change_id.startswith("shared_ws_b_")
        assert clone.workspace_id == "ws_b"
        assert clone.repository_path == "cfg.yaml"
        assert clone.variant_ids == []
        assert clone.payload_id != "pld_shared_cfg"

    def test_does_not_clone_from_target_workspace(self):
        c1 = _change("chg_a", ws="ws_a")
        c2 = _change("chg_b", ws="ws_b")
        plan = _plan(changes=[c1, c2], ws="ws_a")
        # Manually add ws_b workspace plan
        wp_b = VariantWorkspacePlan(
            workspace_id="ws_b", variant_ids=["v1"],
            isolation_mode="shared_workspace",
            base_repository_source_id="src_test", base_commit="a" * 40,
            planned_change_ids=["chg_b"],
        )
        plan = plan.model_copy(update={
            "workspace_plans": list(plan.workspace_plans) + [wp_b],
        })
        clones = clone_shared_changes(plan=plan, target_workspace_id="ws_b")
        cids = [cl.change_id for cl in clones]
        assert all("chg_a" in cid or "chg_b" in cid for cid in cids)

    def test_clones_specific_ids(self):
        c1 = _change("keep", ws="ws_a")
        c2 = _change("skip", ws="ws_a")
        plan = _plan(changes=[c1, c2], ws="ws_a")
        clones = clone_shared_changes(plan=plan, target_workspace_id="ws_b",
                                      change_ids=["keep"])
        assert len(clones) == 1
        assert "keep" in clones[0].change_id

    def test_skips_missing_change(self):
        plan = _plan(changes=[_change("existing", ws="ws_a")], ws="ws_a")
        clones = clone_shared_changes(plan=plan, target_workspace_id="ws_b",
                                      change_ids=["nonexistent"])
        assert len(clones) == 0


# ── build_workspace_binding ──────────────────────────────────────────

class TestBuildWorkspaceBinding:
    def test_returns_new_plan_with_cloned_changes(self):
        c = _change("shared", ws="ws_a", path="cfg.yaml")
        plan = _plan(changes=[c], ws="ws_a")
        target_wp = VariantWorkspacePlan(
            workspace_id="ws_b", variant_ids=["v1"],
            isolation_mode="shared_workspace",
            base_repository_source_id="src_test", base_commit="a" * 40,
        )
        # Add target_wp to plan so build_workspace_binding can find it
        plan = plan.model_copy(update={
            "workspace_plans": list(plan.workspace_plans) + [target_wp],
        })
        new_plan = build_workspace_binding(plan=plan, target_workspace=target_wp)
        assert new_plan is not plan
        assert len(new_plan.changes) == len(plan.changes) + 1
        clone = [cl for cl in new_plan.changes if cl.workspace_id == "ws_b"]
        assert len(clone) == 1

    def test_updated_workspace_plan_includes_cloned_ids(self):
        c = _change("shared", ws="ws_a")
        plan = _plan(changes=[c], ws="ws_a")
        target_wp = VariantWorkspacePlan(
            workspace_id="ws_b", variant_ids=["v1"],
            isolation_mode="shared_workspace",
            base_repository_source_id="src_test", base_commit="a" * 40,
        )
        plan = plan.model_copy(update={
            "workspace_plans": list(plan.workspace_plans) + [target_wp],
        })
        new_plan = build_workspace_binding(plan=plan, target_workspace=target_wp)
        wp_b = [wp for wp in new_plan.workspace_plans if wp.workspace_id == "ws_b"][0]
        assert any("shared" in cid for cid in wp_b.planned_change_ids)

    def test_preserves_original_plan(self):
        c = _change("shared", ws="ws_a")
        plan = _plan(changes=[c], ws="ws_a")
        target_wp = VariantWorkspacePlan(
            workspace_id="ws_b", variant_ids=["v1"],
            isolation_mode="shared_workspace",
            base_repository_source_id="src_test", base_commit="a" * 40,
        )
        plan = plan.model_copy(update={
            "workspace_plans": list(plan.workspace_plans) + [target_wp],
        })
        orig_sha = plan.patch_plan_sha256
        build_workspace_binding(plan=plan, target_workspace=target_wp)
        assert plan.patch_plan_sha256 == orig_sha
        assert len(plan.changes) == 1


# ── merge_workspace_manifests ────────────────────────────────────────

class TestMergeWorkspaceManifests:
    def test_merges_unique_payloads(self):
        p1 = PatchPayload(payload_id="pld_a", change_id="chg_1",
                          payload_kind="full_after_content", target_path="x.py",
                          payload_artifact_id="a.bin", payload_sha256="c" * 64)
        p2 = PatchPayload(payload_id="pld_b", change_id="chg_2",
                          payload_kind="full_after_content", target_path="y.py",
                          payload_artifact_id="b.bin", payload_sha256="d" * 64)
        m1 = _manifest("m1", "ws_a", [p1])
        m2 = _manifest("m2", "ws_b", [p2])
        merged = merge_workspace_manifests(
            manifests=[m1, m2], target_workspace_id="ws_c",
            run_id="run", patch_plan_sha256="c" * 64,
            proposed_diff_sha256="c" * 64,
        )
        assert merged is not None
        assert len(merged.payloads) == 2
        assert merged.workspace_id == "ws_c"

    def test_returns_none_for_empty(self):
        assert merge_workspace_manifests(
            manifests=[], target_workspace_id="ws",
            run_id="run", patch_plan_sha256="c" * 64,
            proposed_diff_sha256="c" * 64,
        ) is None

    def test_deduplicates_by_payload_id(self):
        p = PatchPayload(payload_id="pld_a", change_id="chg_1",
                         payload_kind="full_after_content", target_path="x.py",
                         payload_artifact_id="a.bin", payload_sha256="c" * 64)
        m1 = _manifest("m1", "ws_a", [p])
        m2 = _manifest("m2", "ws_b", [p])
        merged = merge_workspace_manifests(
            manifests=[m1, m2], target_workspace_id="ws_c",
            run_id="run", patch_plan_sha256="c" * 64,
            proposed_diff_sha256="c" * 64,
        )
        assert merged is not None
        assert len(merged.payloads) == 1
