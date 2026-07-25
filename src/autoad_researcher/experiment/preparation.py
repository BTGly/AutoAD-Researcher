"""Durable preparation evidence and stage-level readiness for experiments.

Preparation is deliberately a projection input, not a second experiment
state machine. Agents may append observations and draft actions here; the
execution control plane still owns Session, Attempt, and approval transitions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.gpu_topology import GpuExecutionPlan


PREPARATION_PATH = "experiments/preparation.json"
PreparationEvidenceKind = Literal["observed", "inferred", "verified", "user_decision"]
PreparationAssetKind = Literal[
    "dataset",
    "model_weight",
    "checkpoint",
    "repository",
    "environment",
    "other",
]
PreparationAssetStatus = Literal[
    "unknown",
    "missing",
    "available",
    "verified",
    "failed",
    "awaiting_user",
]
PreparationStageStatus = Literal["unknown", "blocked", "ready", "running", "completed"]


class PreparationEvidence(BaseModel):
    """One bounded observation with enough provenance to audit it later."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1)
    kind: PreparationEvidenceKind
    summary: str = Field(min_length=1)
    repository_ref: str | None = None
    repository_commit: str | None = None
    file_path: str | None = None
    command: list[str] | None = None
    exit_code: int | None = None
    output: str | None = None
    output_sha256: str | None = None
    artifact_ref: str | None = None


class PreparationAsset(BaseModel):
    """An asset and the experiment stages that consume it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    kind: PreparationAssetKind
    status: PreparationAssetStatus = "unknown"
    path: str | None = None
    source: str | None = None
    sha256: str | None = None
    stages: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    user_action_id: str | None = None


class PreparationRepository(BaseModel):
    """A user-supplied baseline or reference repository under investigation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository_id: str = Field(min_length=1)
    role: Literal["baseline", "reference", "candidate"]
    display_name: str = Field(min_length=1)
    path: str | None = None
    remote: str | None = None
    requested_ref: str | None = None
    resolved_commit: str | None = None
    investigation_status: Literal["pending", "running", "complete", "blocked"] = "pending"
    evidence_ids: list[str] = Field(default_factory=list)


class PreparationDecision(BaseModel):
    """A user decision that agents could not infer safely."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: Literal["pending", "answered", "rejected"] = "pending"
    answer: str | None = None
    impact: str = Field(min_length=1)


class PreparationAction(BaseModel):
    """A concrete next action derived from evidence and readiness."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_id: str = Field(min_length=1)
    action_type: Literal["investigate", "provide_path", "verify", "retry", "approve"]
    label: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    available: bool = True
    requires_user: bool = False


class PreparationStage(BaseModel):
    """Readiness for one independently runnable experiment stage."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    stage_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: PreparationStageStatus = "unknown"
    required_asset_ids: list[str] = Field(default_factory=list)
    depends_on_stage_ids: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    approval_required: bool = False


class PreparationExecutionFreeze(BaseModel):
    """Hashes that bind a verified adapter investigation to later Attempts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    repository_fingerprints: dict[str, str] = Field(default_factory=dict)
    adapter_ids: dict[str, str] = Field(default_factory=dict)
    command_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    environment_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    asset_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    preflight_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    frozen_at: str


class ExperimentPreparation(BaseModel):
    """The durable preparation contract projected to the UI and agents."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    status: Literal["unresolved", "investigating", "partially_ready", "ready", "blocked"] = "unresolved"
    current_stage: str | None = None
    repositories: list[PreparationRepository] = Field(default_factory=list)
    assets: list[PreparationAsset] = Field(default_factory=list)
    evidence: list[PreparationEvidence] = Field(default_factory=list)
    user_decisions: list[PreparationDecision] = Field(default_factory=list)
    actions: list[PreparationAction] = Field(default_factory=list)
    stages: list[PreparationStage] = Field(default_factory=list)
    runnable_stage_ids: list[str] = Field(default_factory=list)
    investigation_status: Literal["not_started", "running", "complete", "blocked"] = "not_started"
    gpu_topology: GpuExecutionPlan | None = None
    execution_freeze: PreparationExecutionFreeze | None = None
    updated_at: str | None = None

    @model_validator(mode="after")
    def _validate_references(self):
        asset_ids = {item.asset_id for item in self.assets}
        stage_ids = {item.stage_id for item in self.stages}
        evidence_ids = {item.evidence_id for item in self.evidence}
        action_ids = {item.action_id for item in self.actions}
        if len(asset_ids) != len(self.assets):
            raise ValueError("duplicate preparation asset_id")
        if len(stage_ids) != len(self.stages):
            raise ValueError("duplicate preparation stage_id")
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("duplicate preparation evidence_id")
        if len(action_ids) != len(self.actions):
            raise ValueError("duplicate preparation action_id")
        for stage in self.stages:
            unknown_assets = set(stage.required_asset_ids) - asset_ids
            unknown_dependencies = set(stage.depends_on_stage_ids) - stage_ids
            unknown_actions = set(stage.action_ids) - action_ids
            unknown_evidence = set(stage.evidence_ids) - evidence_ids
            if unknown_assets or unknown_dependencies or unknown_actions or unknown_evidence:
                raise ValueError(
                    f"preparation stage {stage.stage_id!r} references unknown records"
                )
        return self

    def refreshed(self) -> "ExperimentPreparation":
        """Recompute stage blockers without changing agent observations."""

        assets = {item.asset_id: item for item in self.assets}
        original = {item.stage_id: item for item in self.stages}
        refreshed: list[PreparationStage] = []
        for stage in self.stages:
            blockers: list[str] = []
            for asset_id in stage.required_asset_ids:
                asset = assets[asset_id]
                if asset.status in {"missing", "failed", "awaiting_user"}:
                    blockers.append(f"{asset.display_name}: {asset.status}")
                elif asset.status == "unknown":
                    blockers.append(f"{asset.display_name}: unknown")
            for dependency_id in stage.depends_on_stage_ids:
                dependency = original[dependency_id]
                if dependency.status not in {"ready", "running", "completed"}:
                    blockers.append(f"stage {dependency.display_name}: {dependency.status}")
            if stage.status in {"running", "completed"}:
                status = stage.status
            elif blockers:
                status = "blocked"
            elif not stage.required_asset_ids and not stage.evidence_ids:
                status = "unknown"
            else:
                status = "ready"
            refreshed.append(stage.model_copy(update={"status": status, "blockers": blockers}))

        runnable = [stage.stage_id for stage in refreshed if stage.status == "ready"]
        if any(stage.status == "running" for stage in refreshed):
            overall = "investigating"
        elif not refreshed:
            overall = "unresolved"
        elif runnable:
            overall = "ready" if not any(stage.status == "blocked" for stage in refreshed) else "partially_ready"
        elif any(stage.status == "blocked" for stage in refreshed):
            overall = "blocked"
        else:
            overall = "unresolved"
        return self.model_copy(update={"stages": refreshed, "runnable_stage_ids": runnable, "status": overall})


class PreparationStore:
    """Atomic persistence for the preparation sidecar."""

    def load(self, run_dir: Path) -> ExperimentPreparation | None:
        path = run_dir / PREPARATION_PATH
        if not path.is_file():
            return None
        return ExperimentPreparation.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, run_dir: Path, preparation: ExperimentPreparation) -> ExperimentPreparation:
        if preparation.run_id != run_dir.name:
            raise ValueError("preparation run_id does not match run directory")
        normalized = preparation.refreshed()
        path = run_dir / PREPARATION_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(normalized.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return normalized


def empty_preparation(run_id: str) -> ExperimentPreparation:
    """Return an explicit unresolved view; never invent local asset paths."""

    return ExperimentPreparation(run_id=run_id)


def preparation_sha256(preparation: ExperimentPreparation) -> str:
    """Hash the canonical preparation record used by later command plans."""

    return canonical_sha256(preparation)


def require_preparation_stage_if_declared(run_dir: Path, stage_id: str) -> None:
    """Enforce a declared stage gate without inventing a gate for old runs."""

    preparation = PreparationStore().load(run_dir)
    if preparation is None:
        return
    stage = next((item for item in preparation.stages if item.stage_id == stage_id), None)
    if stage is None:
        return
    if stage.status not in {"ready", "running", "completed"}:
        blockers = "; ".join(stage.blockers) or "stage is not ready"
        raise ValueError(f"preparation_stage_blocked:{stage_id}: {blockers}")
