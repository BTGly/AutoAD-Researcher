"""Tests for runner intake dirty-repo validation (_run_intake).

Creates temporary git repos to simulate clean, expected-dirty,
unexpected-dirty, and protected-file-dirty scenarios.
"""

import difflib
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from autoad_researcher.schemas.execution import (
    IntakeCheck,
    RunnerIntakeReport,
    RunnerIntakeRequest,
    WorkspaceExecutionRef,
)
from autoad_researcher.schemas.patch_planning import PatchRunnerHandoff


# ── Helpers ───────────────────────────────────────────────────────────────────


def _compute_diff_sha(repo: Path) -> str:
    """Compute dirty diff SHA using the same approach as ``_compute_dirty_diff_sha256``."""
    result = subprocess.run(
        ["git", "diff", "--name-only"], cwd=repo,
        capture_output=True, text=True, check=True, timeout=15,
    )
    dirty_files = [f for f in result.stdout.strip().splitlines() if f]
    lines: list[str] = []
    for rel_path in dirty_files:
        abs_path = repo / rel_path
        if not abs_path.exists():
            continue
        current = abs_path.read_text(encoding="utf-8")
        head_result = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"], cwd=repo,
            capture_output=True, text=True, timeout=15,
        )
        original = head_result.stdout if head_result.returncode == 0 else ""
        lines.append(f"--- a/{rel_path}")
        lines.append(f"+++ b/{rel_path}")
        diff = difflib.unified_diff(
            original.split("\n"), current.split("\n"),
            fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}", lineterm="",
        )
        lines.extend(list(diff))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with one committed file and return its root."""
    repo = tmp_path / "patchcore-inspection"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # initial file + commit
    (repo / "README.md").write_text("# patchcore-inspection\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


_REAL_SHA = "a" * 64  # non-placeholder SHA for validators that reject "0"*64


def _artifact_ref(**overrides: str) -> dict[str, str]:
    ref = dict(artifact_id="dummy", artifact_type="unknown", locator="runs/_dummy/artifact.json", sha256=_REAL_SHA)
    ref.update(overrides)
    return ref


def _dummy_request(workspace_refs: list[WorkspaceExecutionRef]) -> RunnerIntakeRequest:
    return RunnerIntakeRequest(
        patch_runner_handoff_ref=_artifact_ref(artifact_type="patch_runner_handoff"),
        experiment_planner_handoff_sha256=_REAL_SHA,
        experiment_matrix_sha256=_REAL_SHA,
        shared_protocol_fingerprint="fp_dummy",
        statistical_analysis_plan_sha256=_REAL_SHA,
        operational_guard_policy_sha256=_REAL_SHA,
        workspace_refs=workspace_refs,
    )


def _dummy_handoff(request: RunnerIntakeRequest) -> PatchRunnerHandoff:
    baseline = [ws for ws in request.workspace_refs if ws.subject_type == "baseline"]
    variant = [ws for ws in request.workspace_refs if ws.subject_type == "variant"]
    selected = [vid for ws in variant for vid in ws.variant_ids]
    return PatchRunnerHandoff(
        run_id="test_run",
        repository_before_commit="0" * 40,
        approved_patch_plan_sha256=_REAL_SHA,
        experiment_bundle_ref="test_bundle",
        selected_variant_ids=selected,
        baseline_workspace_ref={
            "workspace_id": baseline[0].workspace_id,
            "repository_fingerprint": "fp_baseline",
            "repository_commit": "0" * 40,
            "repository_validation_ref": _artifact_ref(artifact_type="repo_validation"),
        },
            variant_workspaces=[
                {
                    "workspace_id": vw.workspace_id,
                    "variant_ids": vw.variant_ids,
                    "repository_fingerprint": "fp_variant",
                    "patch_diff_sha256": vw.patch_diff_sha256 or _REAL_SHA,
                    "local_validation_report_sha256": _REAL_SHA,
                    "patch_application_manifest_ref": _artifact_ref(artifact_type="patch_application_manifest"),
                    "post_patch_validation_report_ref": _artifact_ref(artifact_type="post_patch_validation_report"),
                }
                for vw in variant
            ],
    )


def _make_variant_ws(workspace_id: str, diff_sha: str | None) -> WorkspaceExecutionRef:
    return WorkspaceExecutionRef(
        workspace_id=workspace_id,
        subject_type="variant",
        variant_ids=["v1"],
        repository_fingerprint="fp_variant",
        repository_commit="0" * 40,
        patch_diff_sha256=diff_sha,
        local_validation_report_sha256=_REAL_SHA,
        patch_application_manifest_ref=_artifact_ref(artifact_type="patch_application_manifest"),
        post_patch_validation_report_ref=_artifact_ref(artifact_type="post_patch_validation_report"),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRunnerIntakeDirtyRepo:
    """Runner intake dirty-repo validation tests."""

    def test_clean_repo_passes(self, temp_git_repo: Path):
        """Repo is clean -> intake passes."""
        from autoad_researcher.pipeline.runner_execute_stage import _run_intake
        ws_refs = [
            WorkspaceExecutionRef(
                workspace_id="ws_baseline", subject_type="baseline",
                variant_ids=[], repository_fingerprint="fp_baseline",
                repository_commit="0" * 40,
            ),
        ]
        req = _dummy_request(ws_refs)
        result = _run_intake(req, _dummy_handoff(req), temp_git_repo)
        assert result.status == "eligible"
        clean_check = [c for c in result.checks if c.name == "repo_clean"]
        assert clean_check
        assert clean_check[0].status == "passed"

    def test_allows_expected_dirty_diff(self, temp_git_repo: Path):
        """Dirty diff SHA matches variant workspace patch_diff_sha256 -> passes."""
        from autoad_researcher.pipeline.runner_execute_stage import _run_intake
        # Make a real change and compute its diff SHA
        (temp_git_repo / "src" / "patchcore" / "samplers.py").parent.mkdir(parents=True, exist_ok=True)
        (temp_git_repo / "src" / "patchcore" / "samplers.py").write_text(
            "def sample(): pass\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=temp_git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add samplers"],
            cwd=temp_git_repo, capture_output=True, check=True,
        )
        # Now make a second change (this is the "patch" that dirties the repo)
        (temp_git_repo / "src" / "patchcore" / "samplers.py").write_text(
            "def sample():\n    return 42\n"
        )
        expected_sha = _compute_diff_sha(temp_git_repo)

        ws_refs = [
            WorkspaceExecutionRef(
                workspace_id="ws_baseline", subject_type="baseline",
                variant_ids=[], repository_fingerprint="fp_baseline",
                repository_commit="0" * 40,
            ),
            _make_variant_ws("ws_v1", expected_sha),
        ]
        req = _dummy_request(ws_refs)
        result = _run_intake(req, _dummy_handoff(req), temp_git_repo)
        assert result.status == "eligible", (
            f"expected eligible, got {result.status}: "
            f"{[c.details for c in result.checks if c.name == 'repo_clean']}"
        )
        clean_check = [c for c in result.checks if c.name == "repo_clean"]
        assert clean_check
        assert clean_check[0].status == "passed"

    def test_blocks_unexpected_dirty_diff(self, temp_git_repo: Path):
        """Dirty diff does NOT match any variant workspace SHA -> blocked."""
        from autoad_researcher.pipeline.runner_execute_stage import _run_intake
        (temp_git_repo / "src" / "patchcore" / "samplers.py").parent.mkdir(parents=True, exist_ok=True)
        (temp_git_repo / "src" / "patchcore" / "samplers.py").write_text(
            "def sample():\n    return 42\n"
        )

        ws_refs = [
            WorkspaceExecutionRef(
                workspace_id="ws_baseline", subject_type="baseline",
                variant_ids=[], repository_fingerprint="fp_baseline",
                repository_commit="0" * 40,
            ),
            # Deliberately wrong SHA
            _make_variant_ws("ws_v1", "a" * 64),
        ]
        req = _dummy_request(ws_refs)
        result = _run_intake(req, _dummy_handoff(req), temp_git_repo)
        assert result.status == "blocked", (
            f"expected blocked, got {result.status}"
        )
        clean_check = [c for c in result.checks if c.name == "repo_clean"]
        assert clean_check
        assert clean_check[0].status == "failed"

    def test_blocks_protected_file_changes(self, temp_git_repo: Path):
        """Dirty diff includes protected files (bin/, configs/) -> blocked."""
        from autoad_researcher.pipeline.runner_execute_stage import _run_intake
        # Make a real change to variant workspace files
        (temp_git_repo / "src" / "patchcore" / "samplers.py").parent.mkdir(parents=True, exist_ok=True)
        (temp_git_repo / "src" / "patchcore" / "samplers.py").write_text(
            "def sample(): pass\n"
        )
        subprocess.run(["git", "add", "-A"], cwd=temp_git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add samplers"],
            cwd=temp_git_repo, capture_output=True, check=True,
        )
        # Now dirty with both allowed + protected changes
        (temp_git_repo / "src" / "patchcore" / "samplers.py").write_text(
            "def sample():\n    return 42\n"
        )
        (temp_git_repo / "bin").mkdir(parents=True, exist_ok=True)
        (temp_git_repo / "bin" / "eval.py").write_text("print('eval')\n")
        expected_sha = _compute_diff_sha(temp_git_repo)

        ws_refs = [
            WorkspaceExecutionRef(
                workspace_id="ws_baseline", subject_type="baseline",
                variant_ids=[], repository_fingerprint="fp_baseline",
                repository_commit="0" * 40,
            ),
            _make_variant_ws("ws_v1", expected_sha),
        ]
        req = _dummy_request(ws_refs)
        result = _run_intake(req, _dummy_handoff(req), temp_git_repo)
        # Even with SHA match, protected file should cause block
        assert result.status == "blocked", (
            f"expected blocked for protected files, got {result.status}"
        )
        clean_check = [c for c in result.checks if c.name == "repo_clean"]
        assert clean_check
        assert clean_check[0].status == "failed"
        assert "protected" in (clean_check[0].details or "").lower()

    def test_blocks_dirty_diff_without_variant_workspaces(self, temp_git_repo: Path):
        """No variant workspace refs -> dirty repo should still be blocked."""
        from autoad_researcher.pipeline.runner_execute_stage import _run_intake
        (temp_git_repo / "src").mkdir(parents=True, exist_ok=True)
        (temp_git_repo / "src" / "main.py").write_text("x = 1\n")

        ws_refs = [
            WorkspaceExecutionRef(
                workspace_id="ws_baseline", subject_type="baseline",
                variant_ids=[], repository_fingerprint="fp_baseline",
                repository_commit="0" * 40,
            ),
        ]
        req = _dummy_request(ws_refs)
        result = _run_intake(req, _dummy_handoff(req), temp_git_repo)
        assert result.status == "blocked"
