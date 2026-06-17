"""Tests for Repository Intelligence R9 evidence validator."""

import subprocess
from pathlib import Path

from autoad_researcher.repository_intelligence import (
    RepositoryAcquisitionRequest,
    RepositoryAcquisitionRunner,
    RepositoryAnalysisAgent,
    RepositoryIntelligenceRequest,
    synthesize_repository_artifacts,
    validate_repository_intelligence_run,
)


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)


def request() -> RepositoryIntelligenceRequest:
    return RepositoryIntelligenceRequest(
        schema_version=1,
        request_id="req_001",
        run_id="run_demo",
        user_goal="analyze repository",
        discovery_allowed=False,
        user_confirmation_policy="when_ambiguous",
        budget_profile="small",
    )


def make_valid_run(tmp_path: Path):
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    run(["git", "init", "-b", "main"], cwd=source_repo)
    (source_repo / "README.md").write_text("Demo repo\ntrain entrypoint\n", encoding="utf-8")
    (source_repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    run(["git", "add", "README.md", "pyproject.toml"], cwd=source_repo)
    run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"], cwd=source_repo)
    commit = run(["git", "rev-parse", "HEAD"], cwd=source_repo).stdout.strip()
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"
    acquired = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_001",
            workspace_root=workspace,
            remote_url=source_repo.as_posix(),
            resolved_ref="main",
            resolved_commit=commit,
            acquisition_profile="shallow_ref",
        ),
        run_dir=run_dir,
    )
    assert acquired.source is not None
    repo_root = workspace / "repos" / "source_001"
    analysis = RepositoryAnalysisAgent().run_cycle(
        request=request(),
        source=acquired.source,
        repository_root=repo_root,
        run_dir=run_dir,
        iteration=1,
        created_at="2026-06-17T00:00:00Z",
    )
    artifacts = synthesize_repository_artifacts(
        output_dir=run_dir,
        observations=analysis.observations,
        progress=analysis.progress,
    )
    return acquired.source, repo_root, run_dir, artifacts.paths


def test_valid_run_passes_evidence_validation(tmp_path: Path):
    source, repo_root, run_dir, artifacts = make_valid_run(tmp_path)

    report = validate_repository_intelligence_run(
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        artifacts=artifacts,
    )

    assert report.status == "passed"
    assert report.checked_evidence_count >= 2
    assert report.checked_artifact_count == 7


def test_file_sha_mismatch_fails_validation(tmp_path: Path):
    source, repo_root, run_dir, artifacts = make_valid_run(tmp_path)
    (repo_root / "README.md").write_text("tampered\n", encoding="utf-8")

    report = validate_repository_intelligence_run(
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        artifacts=artifacts,
    )

    assert report.status == "failed"
    assert any(issue.code == "EVIDENCE_FILE_SHA_MISMATCH" for issue in report.issues)


def test_claim_referencing_missing_evidence_fails_validation(tmp_path: Path):
    source, repo_root, run_dir, artifacts = make_valid_run(tmp_path)
    payload = (run_dir / "repository_summary.json").read_text(encoding="utf-8")
    (run_dir / "repository_summary.json").write_text(payload.replace("ev_analysis_read_001", "ev_missing"), encoding="utf-8")

    report = validate_repository_intelligence_run(
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        artifacts=artifacts,
    )

    assert report.status == "failed"
    assert any(issue.code == "CLAIM_EVIDENCE_MISSING" for issue in report.issues)
