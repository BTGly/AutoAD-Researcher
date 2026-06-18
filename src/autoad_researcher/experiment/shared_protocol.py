"""Step 1: SharedExperimentProtocol builder.

Assembles the 3.5-internal protocol from adapter output and planning inputs.
"""

import hashlib
import json

from autoad_researcher.schemas.experiment_planning import (
    BaselineExecutionPolicy,
    InterfaceConstraint,
    PlanningInputRefs,
    SharedExperimentProtocol,
    Stage35Input,
    SupplementalEvaluationRefs,
)
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


def build_shared_protocol(
    stage35_input: Stage35Input,
    planning_input_refs: PlanningInputRefs,
    supplemental_refs: SupplementalEvaluationRefs,
    evaluation_protocol_ref: ArtifactReferenceV2,
    baseline_method: str,
    baseline_config_sha256: str,
    seeds: list[int],
    primary_metric: str,
    metric_direction: str,
    protected_paths: list[str],
    must_not_change: list[InterfaceConstraint],
    protocol_evidence_ids: list[str],
    protocol_id: str = "",
    baseline_policy: BaselineExecutionPolicy | None = None,
) -> SharedExperimentProtocol:
    """Construct SharedExperimentProtocol with fingerprint.

    baseline_policy auto-generates ``run_fresh`` if not provided.
    """

    if baseline_policy is None:
        baseline_policy = BaselineExecutionPolicy(
            mode="run_fresh",
            seeds=seeds,
        )

    protocol = SharedExperimentProtocol(
        protocol_id=protocol_id or _generate_protocol_id(),
        schema_version=1,
        planning_input_refs=planning_input_refs,
        supplemental_refs=supplemental_refs,
        evaluation_protocol_ref=evaluation_protocol_ref,
        baseline_method=baseline_method,
        baseline_config_sha256=baseline_config_sha256,
        baseline_policy=baseline_policy,
        seeds=seeds,
        primary_metric=primary_metric,
        metric_direction=metric_direction,
        protected_paths=protected_paths,
        must_not_change=must_not_change,
        protocol_evidence_ids=protocol_evidence_ids,
        protocol_fingerprint="",  # placeholder, replaced below
    )

    fp = _sha_model(protocol)
    protocol.protocol_fingerprint = fp
    protocol.model_validate(protocol.model_dump())  # re-validate with fingerprint set

    return protocol


def _generate_protocol_id() -> str:
    import uuid

    return f"proto_{uuid.uuid4().hex[:8]}"


def _sha_model(model) -> str:
    data = json.dumps(model.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()
