"""Coordinate repository investigation into the durable preparation contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import platform
import sys

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterDraft
from autoad_researcher.experiment.preflight import run_adapter_preflight
from autoad_researcher.experiment.preparation import (
    ExperimentPreparation,
    PreparationAction,
    PreparationAsset,
    PreparationDecision,
    PreparationEvidence,
    PreparationExecutionFreeze,
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
    adapter_drafts: dict[str, ExecutorAdapterDraft] = Field(default_factory=dict)


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
            updated, evidence, artifacts = self._investigate_repository(
                run_dir,
                request.user_goal,
                repository,
                adapter_draft=request.adapter_drafts.get(repository.repository_id),
            )
            updated_repositories.append(updated)
            repository_artifacts[repository.repository_id] = artifacts
            preparation = preparation.model_copy(update={"evidence": _merge_evidence(preparation.evidence, evidence)})
            blocked_repository = blocked_repository or updated.investigation_status == "blocked"

        preparation = preparation.model_copy(update={
            "repositories": updated_repositories,
            "investigation_status": "blocked" if blocked_repository else "complete",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        preparation = preparation.model_copy(update={"execution_freeze": self._freeze_execution(run_dir, preparation)})
        saved = PreparationStore().save(run_dir, preparation)
        return PreparationInvestigationResult(preparation=saved, repository_artifact_refs=repository_artifacts)

    def _investigate_repository(
        self,
        run_dir: Path,
        user_goal: str,
        repository: PreparationRepository,
        adapter_draft: ExecutorAdapterDraft | None = None,
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
        if adapter_draft is not None:
            draft_path = investigation_dir / "adapter_draft.json"
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(adapter_draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
            artifacts.append(str(draft_path.relative_to(run_dir)))
            adapter_result = ExecutorAdapter().inspect_draft(repository_path, adapter_draft)
        else:
            adapter_result = ExecutorAdapter().inspect(repository_path)
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            evidence.append(PreparationEvidence(
                evidence_id=f"repo_{repository.repository_id}_adapter_blocked",
                kind="verified",
                summary=adapter_result.blocker or "adapter evidence is unavailable",
                repository_ref=repository.path,
            ))
            return repository.model_copy(update={"investigation_status": "blocked", "evidence_ids": _merge_ids(repository.evidence_ids, [*repo_evidence_ids, *[item.evidence_id for item in evidence]])}), evidence, artifacts

        evidence.append(PreparationEvidence(
            evidence_id=f"repo_{repository.repository_id}_adapter_inspected",
            kind="observed",
            summary=f"adapter {adapter_result.adapter_id} {'draft' if adapter_result.source == 'agent_draft' else 'manifest'} is structurally valid; backend preflight pending",
            repository_ref=repository.path,
            repository_commit=acquisition.source.resolved_commit,
            file_path="autoad_executor_adapter.json",
        ))
        if adapter_result.evidence.preflight_required or adapter_result.evidence.preflight_commands:
            preflight_path = run_dir / "experiments" / "preflight" / f"investigation_{repository.repository_id}.json"
            preflight = run_adapter_preflight(
                repository_path,
                adapter_result.evidence,
                required_checks=adapter_result.required_preflight_checks or None,
                artifact_path=preflight_path,
            )
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
                return repository.model_copy(update={"investigation_status": "blocked", "evidence_ids": _merge_ids(repository.evidence_ids, [*repo_evidence_ids, *[item.evidence_id for item in evidence]])}), evidence, artifacts
        return repository.model_copy(update={"investigation_status": "complete", "resolved_commit": acquisition.source.resolved_commit, "evidence_ids": _merge_ids(repository.evidence_ids, [*repo_evidence_ids, *[item.evidence_id for item in evidence]])}), evidence, artifacts

    @staticmethod
    def _freeze_execution(run_dir: Path, preparation: ExperimentPreparation) -> PreparationExecutionFreeze | None:
        """Create deterministic hashes from observed inputs, never from prose."""

        if preparation.investigation_status != "complete":
            return None

        adapter_ids: dict[str, str] = {}
        repository_fingerprints: dict[str, str] = {}
        command_material: list[dict[str, object]] = []
        for repository in preparation.repositories:
            if repository.investigation_status != "complete" or not repository.path:
                continue
            repository_path = Path(repository.path).expanduser()
            draft_path = run_dir / "preparation_investigations" / repository.repository_id / "adapter_draft.json"
            if draft_path.is_file():
                try:
                    result = ExecutorAdapter().inspect_draft(
                        repository_path,
                        ExecutorAdapterDraft.model_validate_json(draft_path.read_text(encoding="utf-8")),
                    )
                except Exception:
                    result = ExecutorAdapter().inspect(repository_path)
            else:
                result = ExecutorAdapter().inspect(repository_path)
            if result.status != "supported" or result.evidence is None:
                continue
            adapter_ids[repository.repository_id] = result.evidence.adapter_id
            repository_fingerprints[repository.repository_id] = canonical_sha256({
                "path": repository.path,
                "requested_ref": repository.requested_ref,
                "resolved_commit": repository.resolved_commit,
            })
            command_material.append({
                "repository_id": repository.repository_id,
                "adapter_id": result.evidence.adapter_id,
                "entrypoint": result.evidence.entrypoint,
                "smoke_argv": result.evidence.smoke_argv,
                "evaluation_commands": {
                    name: command.model_dump(mode="json")
                    for name, command in result.evidence.evaluation_commands.items()
                },
                "preflight_commands": {
                    name: command.model_dump(mode="json")
                    for name, command in result.evidence.preflight_commands.items()
                },
                "python_executable": sys.executable,
            })
        if not adapter_ids or len(adapter_ids) != len(preparation.repositories):
            return None
        dataset_hash = _asset_manifest_hash(preparation.assets, kind="dataset")
        asset_hash = _asset_manifest_hash(preparation.assets, kind=None)
        verified_preflight = [
            item.model_dump(mode="json")
            for item in preparation.evidence
            if item.kind == "verified" and item.evidence_id.find("preflight") >= 0
        ]
        for repository in preparation.repositories:
            repository_evidence = [
                item for item in preparation.evidence
                if item.evidence_id in set(repository.evidence_ids)
            ]
            preflight_evidence = [
                item for item in repository_evidence
                if "preflight_" in item.evidence_id
            ]
            if not preflight_evidence or any(item.kind != "verified" for item in preflight_evidence):
                return None
        if not verified_preflight:
            return None
        return PreparationExecutionFreeze(
            repository_fingerprints=repository_fingerprints,
            adapter_ids=adapter_ids,
            command_sha256=canonical_sha256({"commands": command_material}),
            environment_sha256=canonical_sha256({
                "python_executable": sys.executable,
                "python_version": sys.version,
                "platform": platform.platform(),
            }),
            dataset_manifest_sha256=dataset_hash,
            asset_manifest_sha256=asset_hash,
            preflight_sha256=canonical_sha256({"evidence": verified_preflight}) if verified_preflight else None,
            frozen_at=datetime.now(timezone.utc).isoformat(),
        )

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


def _asset_manifest_hash(assets: list[PreparationAsset], *, kind: str | None) -> str | None:
    material: list[dict[str, object]] = []
    for asset in assets:
        if kind is not None and asset.kind != kind:
            continue
        if asset.status not in {"available", "verified"} or not asset.path:
            continue
        root = Path(asset.path).expanduser()
        if not root.exists():
            continue
        if root.is_file():
            material.append({"asset_id": asset.asset_id, "path": asset.path, "size": root.stat().st_size, "sha256": sha256_file(root)})
            continue
        files: list[dict[str, object]] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file() and ".git" not in item.parts):
            files.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        material.append({"asset_id": asset.asset_id, "path": asset.path, "files": files})
    return canonical_sha256({"assets": material}) if material else None


def _merge_ids(existing: list[str], incoming: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *incoming]))


def _merge_evidence(existing: list[PreparationEvidence], incoming: list[PreparationEvidence]) -> list[PreparationEvidence]:
    """Idempotently upsert evidence while preserving first-seen order."""

    merged = {item.evidence_id: item for item in existing}
    order = [item.evidence_id for item in existing]
    for item in incoming:
        if item.evidence_id not in merged:
            order.append(item.evidence_id)
            merged[item.evidence_id] = item
        elif merged[item.evidence_id].model_dump(mode="json") != item.model_dump(mode="json"):
            raise ValueError(f"conflicting preparation evidence for evidence_id: {item.evidence_id}")
    return [merged[evidence_id] for evidence_id in order]
