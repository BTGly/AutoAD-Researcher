"""Coordinate repository investigation into the durable preparation contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.executor_adapters import ExecutorAdapter
from autoad_researcher.experiment.preflight import run_adapter_preflight
from autoad_researcher.experiment.preparation import (
    ExperimentPreparation,
    PreparationAction,
    PreparationAsset,
    PreparationDecision,
    PreparationEvidence,
    PreparationRepository,
    PreparationStage,
    PreparationStore,
    empty_preparation,
)
from autoad_researcher.repository_intelligence.acquisition import (
    RepositoryAcquisitionRequest,
    RepositoryAcquisitionRunner,
)
from autoad_researcher.repository_intelligence.analysis import RepositoryAnalysisAgent
from autoad_researcher.repository_intelligence.models import RepositoryAgentBudget, RepositoryIntelligenceRequest


class PreparationInvestigationRequest(BaseModel):
    """Inputs the user or an upstream agent may provide without CLI internals."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    user_goal: str = Field(min_length=1)
    repositories: list[PreparationRepository] = Field(min_length=1)
    assets: list[PreparationAsset] = Field(default_factory=list)
    stages: list[PreparationStage] = Field(default_factory=list)


class PreparationInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    preparation: ExperimentPreparation
    repository_artifact_refs: dict[str, list[str]] = Field(default_factory=dict)


class ExperimentPreparationCoordinator:
    """Run bounded repository intelligence and persist only auditable findings."""

    def __init__(self, *, acquisition: RepositoryAcquisitionRunner | None = None, analysis: RepositoryAnalysisAgent | None = None):
        self._acquisition = acquisition or RepositoryAcquisitionRunner()
        self._analysis = analysis or RepositoryAnalysisAgent()

    def investigate(self, run_dir: Path, request: PreparationInvestigationRequest) -> PreparationInvestigationResult:
        existing = PreparationStore().load(run_dir)
        preparation = existing or ExperimentPreparation(
            run_id=run_dir.name,
            repositories=request.repositories,
            assets=request.assets,
            stages=request.stages,
        )
        preparation = preparation.model_copy(update={
            "repositories": request.repositories,
            "assets": request.assets or preparation.assets,
            "stages": request.stages or preparation.stages,
            "investigation_status": "running",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        preparation = self._add_asset_actions(preparation)
        repository_artifacts: dict[str, list[str]] = {}
        updated_repositories: list[PreparationRepository] = []
        blocked_repository = False

        for repository in preparation.repositories:
            updated, evidence, artifacts = self._investigate_repository(run_dir, request.user_goal, repository)
            updated_repositories.append(updated)
            repository_artifacts[repository.repository_id] = artifacts
            preparation = preparation.model_copy(update={"evidence": [*preparation.evidence, *evidence]})
            blocked_repository = blocked_repository or updated.investigation_status == "blocked"

        preparation = preparation.model_copy(update={
            "repositories": updated_repositories,
            "investigation_status": "blocked" if blocked_repository else "complete",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        saved = PreparationStore().save(run_dir, preparation)
        return PreparationInvestigationResult(preparation=saved, repository_artifact_refs=repository_artifacts)

    def _investigate_repository(
        self,
        run_dir: Path,
        user_goal: str,
        repository: PreparationRepository,
    ) -> tuple[PreparationRepository, list[PreparationEvidence], list[str]]:
        evidence: list[PreparationEvidence] = []
        artifacts: list[str] = []
        if not repository.path:
            return repository.model_copy(update={"investigation_status": "blocked"}), [
                PreparationEvidence(
                    evidence_id=f"repo_{repository.repository_id}_path_missing",
                    kind="user_decision",
                    summary="repository path is unresolved; user input is required",
                )
            ], artifacts

        repository_path = Path(repository.path).expanduser()
        if not repository_path.is_dir():
            return repository.model_copy(update={"investigation_status": "blocked"}), [
                PreparationEvidence(
                    evidence_id=f"repo_{repository.repository_id}_path_invalid",
                    kind="verified",
                    summary=f"repository path is not a directory: {repository.path}",
                    repository_ref=repository.path,
                )
            ], artifacts

        investigation_dir = run_dir / "preparation_investigations" / repository.repository_id
        acquisition = self._acquisition.acquire(
            RepositoryAcquisitionRequest(
                schema_version=1,
                source_id=repository.repository_id,
                workspace_root=investigation_dir / "workspace",
                local_path=repository_path,
                resolved_ref=repository.requested_ref,
                acquisition_profile="local",
            ),
            run_dir=investigation_dir,
        )
        if acquisition.status != "success" or acquisition.source is None:
            return repository.model_copy(update={"investigation_status": "blocked"}), [
                PreparationEvidence(
                    evidence_id=f"repo_{repository.repository_id}_acquisition_failed",
                    kind="verified",
                    summary=acquisition.error_message or "repository acquisition failed",
                    repository_ref=repository.path,
                    artifact_ref=str((investigation_dir / "acquisition_tool_calls.jsonl").relative_to(run_dir)),
                )
            ], artifacts

        artifacts.extend([
            str(path.relative_to(run_dir))
            for path in [investigation_dir / "repository_source.json", investigation_dir / "repository_attestation.json"]
            if path.is_file()
        ])
        analysis_request = RepositoryIntelligenceRequest(
            schema_version=1,
            request_id=f"req_{repository.repository_id}_{run_dir.name}",
            run_id=run_dir.name,
            user_goal=user_goal,
            local_path=repository.path,
            discovery_allowed=False,
            user_confirmation_policy="when_ambiguous",
            budget_profile="small",
            budget=RepositoryAgentBudget(
                max_total_tool_calls=12, max_total_llm_calls=0, max_total_input_tokens=0, max_total_output_tokens=0,
                max_discovery_search_calls=0, max_discovery_fetch_calls=0, max_analysis_tool_calls=8,
                max_analysis_file_reads=4, max_analysis_search_calls=4, max_analysis_llm_calls=0,
                max_repair_tool_calls=0, max_repair_llm_calls=0, max_repairs=0,
            ),
        )
        analysis = self._analysis.run_cycle(
            request=analysis_request,
            source=acquisition.source,
            repository_root=repository_path,
            run_dir=investigation_dir,
            iteration=1,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        artifacts.append(str((investigation_dir / "evidence_index.jsonl").relative_to(run_dir)))
        repo_evidence_ids = [f"repo_{repository.repository_id}_obs_{item.observation_id}" for item in analysis.observations]
        evidence.extend(
            PreparationEvidence(
                evidence_id=evidence_id,
                kind="observed" if item.status == "candidate" else "verified",
                summary=item.summary,
                repository_ref=repository.path,
                repository_commit=acquisition.source.resolved_commit,
                artifact_ref=str((investigation_dir / "evidence_index.jsonl").relative_to(run_dir)),
            )
            for evidence_id, item in zip(repo_evidence_ids, analysis.observations, strict=False)
        )
        adapter_result = ExecutorAdapter().inspect(repository_path)
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            evidence.append(PreparationEvidence(
                evidence_id=f"repo_{repository.repository_id}_adapter_blocked",
                kind="verified",
                summary=adapter_result.blocker or "adapter evidence is unavailable",
                repository_ref=repository.path,
            ))
            return repository.model_copy(update={"investigation_status": "blocked", "evidence_ids": [*repository.evidence_ids, *repo_evidence_ids]}), evidence, artifacts

        evidence.append(PreparationEvidence(
            evidence_id=f"repo_{repository.repository_id}_adapter_inspected",
            kind="observed",
            summary=f"adapter {adapter_result.adapter_id} manifest is structurally valid",
            repository_ref=repository.path,
            repository_commit=acquisition.source.resolved_commit,
            file_path="autoad_executor_adapter.json",
        ))
        if adapter_result.evidence.preflight_required or adapter_result.evidence.preflight_commands:
            preflight_path = run_dir / "experiments" / "preflight" / f"investigation_{repository.repository_id}.json"
            preflight = run_adapter_preflight(repository_path, adapter_result.evidence, artifact_path=preflight_path)
            artifacts.append(str(preflight_path.relative_to(run_dir)))
            evidence.extend(
                PreparationEvidence(
                    evidence_id=f"repo_{repository.repository_id}_preflight_{check.name}",
                    kind="verified" if check.status == "passed" else "observed",
                    summary=f"preflight {check.name}: {check.status}",
                    repository_ref=repository.path,
                    command=check.command,
                    exit_code=check.exit_code,
                    output=check.stdout,
                    output_sha256=check.stdout_sha256,
                    artifact_ref=str(preflight_path.relative_to(run_dir)),
                )
                for check in preflight.checks
            )
            if not preflight.passed:
                return repository.model_copy(update={"investigation_status": "blocked", "evidence_ids": [*repository.evidence_ids, *repo_evidence_ids]}), evidence, artifacts
        return repository.model_copy(update={"investigation_status": "complete", "resolved_commit": acquisition.source.resolved_commit, "evidence_ids": [*repository.evidence_ids, *repo_evidence_ids]}), evidence, artifacts

    @staticmethod
    def _add_asset_actions(preparation: ExperimentPreparation) -> ExperimentPreparation:
        actions = list(preparation.actions)
        decisions = list(preparation.user_decisions)
        assets: list[PreparationAsset] = []
        for asset in preparation.assets:
            if asset.status not in {"missing", "awaiting_user"}:
                assets.append(asset)
                continue
            action_id = asset.user_action_id or f"provide_{asset.asset_id}"
            if not any(item.action_id == action_id for item in actions):
                actions.append(PreparationAction(
                    action_id=action_id,
                    action_type="provide_path",
                    label=f"提供 {asset.display_name} 路径",
                    target_id=asset.asset_id,
                    requires_user=True,
                ))
            decision_id = f"decision_{asset.asset_id}_path"
            if not any(item.decision_id == decision_id for item in decisions):
                decisions.append(PreparationDecision(
                    decision_id=decision_id,
                    question=f"请提供 {asset.display_name} 的本地目录路径。",
                    impact="只阻断使用该资产的实验阶段。",
                ))
            assets.append(asset.model_copy(update={"user_action_id": action_id}))
        return preparation.model_copy(update={"assets": assets, "actions": actions, "user_decisions": decisions})
