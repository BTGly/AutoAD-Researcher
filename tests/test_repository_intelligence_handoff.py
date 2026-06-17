"""Tests for Repository Intelligence R11 EnvironmentPlan handoff."""

import json
from pathlib import Path

from autoad_researcher.environments import evaluate_environment_plan_policy
from autoad_researcher.repository_intelligence import RepositorySource, build_environment_plan_handoff


SHA = "a" * 64
COMMIT = "b" * 40


def source() -> RepositorySource:
    return RepositorySource(
        schema_version=1,
        source_id="source_001",
        kind="github_public",
        canonical_remote_url="https://github.com/example/repo",
        requested_ref="main",
        acquisition_profile="shallow_ref",
        resolved_commit=COMMIT,
        tree_sha=SHA,
        detached_head=True,
        dirty=False,
        local_path_label="workspace/repos/source_001",
        source_fingerprint=SHA,
    )


def write_artifacts(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "dependency_evidence.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependency_declaration_files": [
                    {
                        "claim_id": "claim_dependency_files",
                        "status": "confirmed",
                        "confidence": "low",
                        "summary": "dependency file",
                        "evidence_ids": ["ev_pyproject"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (path / "environment_context.json").write_text(json.dumps({"schema_version": 1, "final_decision": False}), encoding="utf-8")
    (path / "uncertainties.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "groups": [
                    {
                        "category": "blocking_environment_plan",
                        "items": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_handoff_builds_policy_checked_environment_plan_candidate(tmp_path: Path):
    write_artifacts(tmp_path)

    result = build_environment_plan_handoff(
        run_id="run_demo",
        source=source(),
        artifact_dir=tmp_path,
        output_path=tmp_path / "environment_plan_candidate.json",
    )

    assert result.executed is False
    assert result.plan.created_by == "llm"
    assert result.plan.evidence[0].path_or_id == "ev_pyproject"
    assert result.plan.assumptions[0].risk == "medium"
    assert result.plan.target.repository_path == "workspace/repos/source_001"
    assert result.policy_report.status == "passed"
    assert (tmp_path / "environment_plan_candidate.json").is_file()


def test_environment_handoff_policy_blocks_high_risk_repository_modification(tmp_path: Path):
    write_artifacts(tmp_path)
    result = build_environment_plan_handoff(
        run_id="run_demo",
        source=source(),
        artifact_dir=tmp_path,
        output_path=tmp_path / "environment_plan_candidate.json",
    )
    plan = result.plan.model_copy(deep=True)
    plan.build_steps[0].modifies_repository = True

    report = evaluate_environment_plan_policy(plan)

    assert report.status == "denied"
    assert any(v.code == "ENV_APPROVAL_REQUIRED" for v in report.violations)
