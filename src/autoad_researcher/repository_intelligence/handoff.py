"""Handoff from Repository Intelligence to EnvironmentPlan."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.environments import (
    CommandStep,
    EnvironmentPermissions,
    EnvironmentPlan,
    EnvironmentPlanPolicyReport,
    EnvironmentTarget,
    EvidenceReference,
    PlanAssumption,
    ValidationStep,
    evaluate_environment_plan_policy,
    write_environment_plan,
)
from autoad_researcher.repository_intelligence.models import RepositorySource


class EnvironmentPlanHandoffResult(BaseModel):
    """EnvironmentPlan candidate plus policy report."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    plan: EnvironmentPlan
    policy_report: EnvironmentPlanPolicyReport
    output_path: str
    executed: Literal[False]


def build_environment_plan_handoff(
    *,
    run_id: str,
    source: RepositorySource,
    artifact_dir: Path,
    output_path: Path,
) -> EnvironmentPlanHandoffResult:
    """Build and write an EnvironmentPlan candidate from repository artifacts."""
    dependency_payload = _read_json(artifact_dir / "dependency_evidence.json")
    environment_payload = _read_json(artifact_dir / "environment_context.json")
    uncertainties_payload = _read_json(artifact_dir / "uncertainties.json")
    dependency_evidence_ids = _collect_evidence_ids(dependency_payload)

    evidence = [
        EvidenceReference(
            source_type="repository",
            path_or_id=evidence_id,
            claim="Repository analysis evidence relevant to environment planning.",
            sha256=None,
        )
        for evidence_id in dependency_evidence_ids
    ]
    if not evidence:
        evidence = [
            EvidenceReference(
                source_type="repository",
                path_or_id="environment_context.json",
                claim="Repository environment context artifact was produced.",
                sha256=sha256_file(artifact_dir / "environment_context.json"),
            )
        ]

    assumptions = _assumptions_from_uncertainties(uncertainties_payload)
    repo_path = f"workspace/repos/{source.source_id}"
    env_path = f"workspace/envs/{source.source_id}"
    plan = EnvironmentPlan(
        schema_version=1,
        plan_id=f"env_plan_{source.source_id}_v0",
        run_id=run_id,
        revision=0,
        parent_plan_id=None,
        target=EnvironmentTarget(
            kind="python_uv_venv",
            environment_path=env_path,
            runtime_requirements={"python": "3.11"},
            repository_path=repo_path,
        ),
        evidence=evidence,
        assumptions=assumptions,
        build_steps=[
            CommandStep(
                step_id="create_env",
                program="uv",
                args=["venv", env_path, "--python", "3.11"],
                cwd=repo_path,
                environment={"UV_LINK_MODE": "copy"},
                timeout_seconds=120,
                network=False,
                modifies_repository=False,
                requires_approval=False,
            )
        ],
        validation_steps=[
            ValidationStep(
                validation_id="check_python",
                kind="runtime_version",
                parameters={"python": "3.11"},
                required=True,
                timeout_seconds=30,
                network=False,
            )
        ],
        permissions=EnvironmentPermissions(
            network_during_build=False,
            network_during_validation=False,
            allow_system_package_install=False,
            allow_repository_modification=False,
            allow_global_environment_mutation=False,
            max_revision_count=2,
        ),
        created_by="llm",
    )
    policy_report = evaluate_environment_plan_policy(plan)
    write_environment_plan(plan, output_path)
    return EnvironmentPlanHandoffResult(
        schema_version=1,
        plan=plan,
        policy_report=policy_report,
        output_path=output_path.as_posix(),
        executed=False,
    )


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_evidence_ids(value) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for evidence_id in value.get("evidence_ids", []):
            if evidence_id not in ids:
                ids.append(evidence_id)
        for child in value.values():
            for evidence_id in _collect_evidence_ids(child):
                if evidence_id not in ids:
                    ids.append(evidence_id)
    elif isinstance(value, list):
        for child in value:
            for evidence_id in _collect_evidence_ids(child):
                if evidence_id not in ids:
                    ids.append(evidence_id)
    return ids


def _assumptions_from_uncertainties(payload) -> list[PlanAssumption]:
    assumptions: list[PlanAssumption] = []
    for index, group in enumerate(payload.get("groups", []), 1):
        category = group.get("category", "unknown")
        assumptions.append(
            PlanAssumption(
                assumption_id=f"repo_uncertainty_{index:03d}",
                statement=f"Repository uncertainty remains before environment build: {category}",
                risk="medium",
                validation_id=None,
            )
        )
    return assumptions
