"""Structured Repository Intelligence artifact schemas and synthesis for R8."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.repository_intelligence.analysis import AnalysisObservation, AnalysisProgress
from autoad_researcher.repository_intelligence.ids import IdentifierPattern, validate_relative_path
from autoad_researcher.repository_intelligence.models import RepositoryArtifactPaths
from autoad_researcher.repository_intelligence.status import ClaimStatus, Confidence


class ArtifactClaim(BaseModel):
    """Evidence-backed claim used inside synthesized artifacts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(pattern=IdentifierPattern)
    status: ClaimStatus
    confidence: Confidence
    summary: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None

    @model_validator(mode="after")
    def _validate_evidence(self):
        if self.status == "confirmed" and not self.evidence_ids:
            raise ValueError("confirmed artifact claim requires evidence_ids")
        if self.status == "inferred" and not self.rationale_summary:
            raise ValueError("inferred artifact claim requires rationale_summary")
        return self


class RepositorySummaryArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    repository_purpose: ArtifactClaim
    research_task: ArtifactClaim
    main_languages_and_frameworks: list[ArtifactClaim]
    core_modules: list[ArtifactClaim]
    execution_overview: ArtifactClaim
    known_limits: list[ArtifactClaim]


class EntrypointCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=IdentifierPattern)
    path: str | None = None
    status: ClaimStatus
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str | None) -> str | None:
        return None if value is None else validate_relative_path(value)


class EntrypointsArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    primary_train: EntrypointCandidate
    primary_inference: EntrypointCandidate
    primary_evaluation: EntrypointCandidate
    data_preparation: EntrypointCandidate
    tests: list[EntrypointCandidate]
    alternatives: list[EntrypointCandidate]
    unresolved_candidates: list[EntrypointCandidate]


class DependencyEvidenceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    runtime_version_constraints: list[ArtifactClaim]
    package_manager_evidence: list[ArtifactClaim]
    dependency_declaration_files: list[ArtifactClaim]
    lockfile_evidence: list[ArtifactClaim]
    direct_dependencies: list[ArtifactClaim]
    dev_or_optional_groups: list[ArtifactClaim]
    system_dependency_signals: list[ArtifactClaim]
    cuda_or_custom_extension_signals: list[ArtifactClaim]
    undeclared_import_signals: list[ArtifactClaim]
    conflicts: list[ArtifactClaim]


class PathPolicyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str
    category: Literal["modifiable_candidate", "protected_candidate", "generated_or_vendor", "unknown"]
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class ModifiablePathsArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    policy_status: Literal["proposal"]
    paths: list[PathPolicyEntry]


class EvaluationContractDraftArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    status: Literal["draft"]
    evaluator_candidates: list[ArtifactClaim]
    metrics_and_direction: list[ArtifactClaim]
    result_source_candidates: list[ArtifactClaim]
    dataset_split_evidence: list[ArtifactClaim]
    post_processing_evidence: list[ArtifactClaim]
    protected_paths: list[PathPolicyEntry]
    unresolved_risks: list[ArtifactClaim]


class EnvironmentContextArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    final_decision: Literal[False]
    runtime_candidates: list[ArtifactClaim]
    package_manager_candidates: list[ArtifactClaim]
    dependency_files: list[ArtifactClaim]
    lockfiles: list[ArtifactClaim]
    install_command_candidates: list[ArtifactClaim]
    system_dependency_signals: list[ArtifactClaim]
    accelerator_signals: list[ArtifactClaim]
    custom_build_signals: list[ArtifactClaim]
    external_asset_signals: list[ArtifactClaim]
    recommended_validations: list[ArtifactClaim]
    conflicts: list[ArtifactClaim]


class UncertaintyGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "blocking_environment_plan",
        "blocking_entrypoint_selection",
        "blocking_evaluation_contract",
        "blocking_dataset_asset_access",
        "scientific_validity_risks",
        "low_priority_unknowns",
    ]
    items: list[ArtifactClaim]


class UncertaintiesArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    groups: list[UncertaintyGroup]


class SynthesizedRepositoryArtifacts(BaseModel):
    """Paths and SHA256 for seven formal artifacts."""

    model_config = ConfigDict(extra="forbid")

    paths: RepositoryArtifactPaths
    artifact_sha256: dict[str, str]

    @model_validator(mode="after")
    def _validate_artifact_sha256(self):
        if set(self.artifact_sha256) != self.paths.path_set():
            raise ValueError("artifact_sha256 keys must match formal artifact paths")
        for path, sha in self.artifact_sha256.items():
            validate_relative_path(path)
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError(f"artifact sha must be sha256 hex: {path}")
        return self


def synthesize_repository_artifacts(
    *,
    output_dir: Path,
    observations: list[AnalysisObservation],
    progress: AnalysisProgress,
) -> SynthesizedRepositoryArtifacts:
    """Synthesize seven formal artifacts from analysis evidence only."""
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_ids = _evidence_ids(observations)
    repository_summary = RepositorySummaryArtifact(
        schema_version=1,
        repository_purpose=_claim(
            "claim_repository_purpose",
            "inferred",
            "Repository purpose requires synthesis review.",
            rationale_summary="R8 deterministic skeleton does not infer scientific purpose.",
        ),
        research_task=_claim("claim_research_task", "unknown", "Research task is not confirmed yet."),
        main_languages_and_frameworks=[],
        core_modules=[],
        execution_overview=_claim("claim_execution_overview", "unknown", "Execution flow remains unresolved."),
        known_limits=[],
    )
    if progress.coverage.get("repository_summary") == "confirmed" and evidence_ids:
        repository_summary.repository_purpose = _claim(
            "claim_repository_purpose",
            "confirmed",
            "Repository documentation was read during analysis.",
            evidence_ids=evidence_ids[:1],
        )

    entrypoint_evidence = _category_evidence(observations, "entrypoints")
    entrypoints = EntrypointsArtifact(
        schema_version=1,
        primary_train=EntrypointCandidate(
            name="primary_train",
            path=None,
            status="inferred" if entrypoint_evidence else "unknown",
            evidence_ids=entrypoint_evidence,
        ),
        primary_inference=EntrypointCandidate(name="primary_inference", path=None, status="unknown"),
        primary_evaluation=EntrypointCandidate(name="primary_evaluation", path=None, status="unknown"),
        data_preparation=EntrypointCandidate(name="data_preparation", path=None, status="unknown"),
        tests=[],
        alternatives=[],
        unresolved_candidates=[],
    )

    dependency_evidence = _category_evidence(observations, "dependencies")
    dependency_artifact = DependencyEvidenceArtifact(
        schema_version=1,
        runtime_version_constraints=[],
        package_manager_evidence=[],
        dependency_declaration_files=[
            _claim("claim_dependency_files", "confirmed", "Dependency declaration evidence was observed.", evidence_ids=dependency_evidence)
        ] if dependency_evidence else [],
        lockfile_evidence=[],
        direct_dependencies=[],
        dev_or_optional_groups=[],
        system_dependency_signals=[],
        cuda_or_custom_extension_signals=[],
        undeclared_import_signals=[],
        conflicts=[],
    )

    path_policy = ModifiablePathsArtifact(
        schema_version=1,
        policy_status="proposal",
        paths=[
            PathPolicyEntry(
                path="README.md",
                category="unknown",
                rationale="R8 synthesis does not authorize modifications.",
                evidence_ids=_category_evidence(observations, "repository_summary"),
            )
        ],
    )
    evaluation = EvaluationContractDraftArtifact(
        schema_version=1,
        status="draft",
        evaluator_candidates=[],
        metrics_and_direction=[],
        result_source_candidates=[],
        dataset_split_evidence=[],
        post_processing_evidence=[],
        protected_paths=[],
        unresolved_risks=[_claim("claim_eval_unknown", "unknown", "Evaluation contract requires more repository evidence.")],
    )
    environment = EnvironmentContextArtifact(
        schema_version=1,
        final_decision=False,
        runtime_candidates=[],
        package_manager_candidates=[],
        dependency_files=[
            _claim("claim_env_dependency_files", "confirmed", "Dependency files may inform environment planning.", evidence_ids=dependency_evidence)
        ] if dependency_evidence else [],
        lockfiles=[],
        install_command_candidates=[],
        system_dependency_signals=[],
        accelerator_signals=[],
        custom_build_signals=[],
        external_asset_signals=[],
        recommended_validations=[_claim("claim_env_validate", "unknown", "Environment builder must validate dependencies separately.")],
        conflicts=[],
    )
    uncertainties = UncertaintiesArtifact(
        schema_version=1,
        groups=[
            UncertaintyGroup(
                category="blocking_entrypoint_selection",
                items=[_claim("claim_entrypoint_unknown", "unknown", "Primary entrypoints are not confirmed.")],
            ),
            UncertaintyGroup(
                category="blocking_evaluation_contract",
                items=[_claim("claim_evaluation_unknown", "unknown", "Evaluation contract remains draft.")],
            ),
        ],
    )

    paths = RepositoryArtifactPaths(
        repository_summary="repository_summary.json",
        entrypoints="entrypoints.json",
        dependency_evidence="dependency_evidence.json",
        modifiable_paths="modifiable_paths.json",
        evaluation_contract_draft="evaluation_contract_draft.json",
        environment_context="environment_context.json",
        uncertainties="uncertainties.json",
    )
    payloads: dict[str, BaseModel] = {
        paths.repository_summary: repository_summary,
        paths.entrypoints: entrypoints,
        paths.dependency_evidence: dependency_artifact,
        paths.modifiable_paths: path_policy,
        paths.evaluation_contract_draft: evaluation,
        paths.environment_context: environment,
        paths.uncertainties: uncertainties,
    }
    artifact_sha256: dict[str, str] = {}
    for relative_path, payload in payloads.items():
        target = output_dir / relative_path
        _write_json_atomic(target, payload)
        artifact_sha256[relative_path] = sha256_file(target)
    return SynthesizedRepositoryArtifacts(paths=paths, artifact_sha256=artifact_sha256)


def _claim(
    claim_id: str,
    status: ClaimStatus,
    summary: str,
    *,
    evidence_ids: list[str] | None = None,
    confidence: Confidence = "low",
    rationale_summary: str | None = None,
) -> ArtifactClaim:
    return ArtifactClaim(
        claim_id=claim_id,
        status=status,
        confidence=confidence,
        summary=summary,
        evidence_ids=evidence_ids or [],
        rationale_summary=rationale_summary,
    )


def _evidence_ids(observations: list[AnalysisObservation]) -> list[str]:
    ids: list[str] = []
    for observation in observations:
        for evidence_id in observation.evidence_ids:
            if evidence_id not in ids:
                ids.append(evidence_id)
    return ids


def _category_evidence(observations: list[AnalysisObservation], category: str) -> list[str]:
    ids: list[str] = []
    for observation in observations:
        if observation.category != category:
            continue
        for evidence_id in observation.evidence_ids:
            if evidence_id not in ids:
                ids.append(evidence_id)
    return ids


def _write_json_atomic(path: Path, value: BaseModel) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
