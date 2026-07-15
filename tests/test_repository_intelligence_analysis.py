"""Tests for Repository Intelligence R7 analysis agent."""

import subprocess
from pathlib import Path

from autoad_researcher.repository_intelligence import (
    RepositoryAcquisitionRequest,
    RepositoryAcquisitionRunner,
    RepositoryAnalysisAgent,
    RepositoryIntelligenceRequest,
    budget_for_profile,
    read_evidence_index,
)


def request(**overrides) -> RepositoryIntelligenceRequest:
    data = {
        "schema_version": 1,
        "request_id": "req_001",
        "run_id": "run_demo",
        "user_goal": "analyze repository",
        "discovery_allowed": False,
        "user_confirmation_policy": "when_ambiguous",
        "budget_profile": "small",
    }
    data.update(overrides)
    return RepositoryIntelligenceRequest(**data)


def make_acquired_repo(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-b", "main"], cwd=source)
    (source / "README.md").write_text("PatchCore example\nRun train.py for training.\n", encoding="utf-8")
    (source / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (source / "train.py").write_text("def train():\n    pass\n", encoding="utf-8")
    run(["git", "add", "README.md", "pyproject.toml", "train.py"], cwd=source)
    run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"], cwd=source)
    commit = run(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()
    run_dir = tmp_path / "run"
    workspace = tmp_path / "workspace"
    acquired = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_001",
            workspace_root=workspace,
            remote_url=source.as_posix(),
            resolved_ref="main",
            resolved_commit=commit,
            acquisition_profile="shallow_ref",
        ),
        run_dir=run_dir,
    )
    assert acquired.source is not None
    return acquired.source, workspace / "repos" / "source_001", run_dir


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)


def test_budget_profiles_match_plan_baselines():
    small = budget_for_profile("small")
    medium = budget_for_profile("medium")
    large = budget_for_profile("large")

    assert small.max_analysis_tool_calls == 50
    assert small.max_analysis_file_reads == 24
    assert small.max_analysis_search_calls == 12
    assert medium.max_analysis_tool_calls == 90
    assert large.max_analysis_tool_calls == 140
    assert large.max_no_progress_cycles == 2


def test_analysis_cycle_writes_progress_observations_signal_and_evidence(tmp_path: Path):
    source, repo_root, run_dir = make_acquired_repo(tmp_path)

    result = RepositoryAnalysisAgent().run_cycle(
        request=request(),
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        iteration=1,
        created_at="2026-06-17T00:00:00Z",
    )

    assert result.progress.file_reads_used >= 2
    assert result.progress.search_calls_used == 0
    assert result.control_signal.new_evidence_count >= 2
    assert result.progress.coverage["repository_summary"] == "confirmed"
    assert result.progress.coverage["dependencies"] == "confirmed"
    assert (run_dir / "analysis_progress.json").is_file()
    assert (run_dir / "analysis_observations.jsonl").is_file()
    assert (run_dir / "analysis_control_signals.jsonl").is_file()
    evidence = read_evidence_index(run_dir / "evidence_index.jsonl")
    assert any(record.evidence.source_kind == "repository_file" for record in evidence)
    assert not any(observation.category == "entrypoints" for observation in result.observations)


def test_analysis_observations_are_brief_and_evidence_backed(tmp_path: Path):
    source, repo_root, run_dir = make_acquired_repo(tmp_path)

    result = RepositoryAnalysisAgent().run_cycle(
        request=request(),
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        iteration=1,
        created_at="2026-06-17T00:00:00Z",
    )

    assert result.observations
    assert all(len(observation.summary.split()) <= 12 for observation in result.observations)
    assert all(observation.evidence_ids for observation in result.observations)


def test_no_progress_limit_forces_synthesis(tmp_path: Path):
    source, repo_root, run_dir = make_acquired_repo(tmp_path)

    result = RepositoryAnalysisAgent().run_cycle(
        request=request(),
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        iteration=1,
        created_at="2026-06-17T00:00:00Z",
        no_progress_cycles=2,
    )

    assert result.transition.decision == "forced_synthesis"
    assert result.progress.stage_status == "forced_complete"


def test_analysis_cycle_does_not_record_process_tool_calls(tmp_path: Path):
    source, repo_root, run_dir = make_acquired_repo(tmp_path)
    before = (run_dir / "acquisition_tool_calls.jsonl").read_text(encoding="utf-8")

    RepositoryAnalysisAgent().run_cycle(
        request=request(),
        source=source,
        repository_root=repo_root,
        run_dir=run_dir,
        iteration=1,
        created_at="2026-06-17T00:00:00Z",
    )

    after = (run_dir / "acquisition_tool_calls.jsonl").read_text(encoding="utf-8")
    assert after == before
