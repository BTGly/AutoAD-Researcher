from autoad_researcher.experiment.failure_classifier import DetectorProfile, FailureClassification
from autoad_researcher.experiment.failure_policy import FailurePolicy, FailurePolicyContext


def _classification(code: str, *, retryable=False, category="run_failed") -> FailureClassification:
    return FailureClassification(
        profile=DetectorProfile.GPU_TRAINING,
        enabled_detectors=["fixture"],
        matched_detector="fixture",
        failure_code=code,
        attempt_category=category,
        retryable=retryable,
    )


def _context(**updates) -> FailurePolicyContext:
    values = {
        "retry_count": 0,
        "max_retries": 2,
        "repair_count": 0,
        "max_repairs": 1,
    }
    values.update(updates)
    return FailurePolicyContext.model_validate(values)


def test_oom_requires_explicit_bounded_repair_authority():
    policy = FailurePolicy()
    assert policy.decide(classification=_classification("OOM", retryable=True), context=_context()).action == "no_retry"
    decision = policy.decide(
        classification=_classification("OOM", retryable=True),
        context=_context(oom_repair_authorized=True),
    )
    assert decision.action == "bounded_repair"
    assert decision.consumes_repair
    assert policy.decide(
        classification=_classification("OOM", retryable=True),
        context=_context(oom_repair_authorized=True, repair_count=1),
    ).action == "no_retry"


def test_nan_and_protocol_violation_are_not_infrastructure_retries():
    policy = FailurePolicy()
    assert policy.decide(classification=_classification("NAN_OR_INF"), context=_context()).action == "coordinator_review"
    assert policy.decide(
        classification=_classification("PROTECTED_ARTIFACT_CHANGED", category="protocol_violated"),
        context=_context(),
    ).action == "reject_protocol"


def test_worker_lost_retries_only_within_explicit_cap():
    policy = FailurePolicy()
    retry = policy.decide(
        classification=_classification("WORKER_LOST", retryable=True),
        context=_context(retry_count=1, max_retries=2),
    )
    assert retry.action == "retry_infrastructure"
    assert retry.consumes_retry
    assert policy.decide(
        classification=_classification("WORKER_LOST", retryable=True),
        context=_context(retry_count=2, max_retries=2),
    ).action == "archive_failure"


def test_timeout_with_progress_gets_at_most_one_authorized_extension():
    policy = FailurePolicy()
    assert policy.decide(
        classification=_classification("RUN_TIMEOUT"),
        context=_context(timeout_progress_observed=False, timeout_extension_authorized=True),
    ).action == "no_retry"
    allowed = policy.decide(
        classification=_classification("RUN_TIMEOUT"),
        context=_context(timeout_progress_observed=True, timeout_extension_authorized=True, retry_count=0),
    )
    assert allowed.action == "increase_timeout_retry"
    assert allowed.consumes_retry
    assert policy.decide(
        classification=_classification("RUN_TIMEOUT"),
        context=_context(timeout_progress_observed=True, timeout_extension_authorized=True, retry_count=1),
    ).action == "no_retry"
