from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.health_diagnosis import HealthDiagnosisAgent
from autoad_researcher.experiment.retry_policy import RetryPolicy
from tests.test_experiment_attempt_job import _plan, _refs

def _attempt(code: str) -> ExperimentAttempt:
    plan = _plan()
    return ExperimentAttempt(attempt_id="attempt_000001", run_id="run", session_id="session", idempotency_key="key", job_type="experiment_baseline", attempt_purpose="baseline", command_plan=plan, input_refs=_refs(plan), job_timeout_sec=10, max_retries=1, runtime_status="FAILED", failure_code=code, created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00")

def test_retry_policy_only_retries_known_infrastructure_failures():
    retryable = FailureClassification(profile=DetectorProfile.GPU_TRAINING, enabled_detectors=["execution_failure"], matched_detector="execution_failure", failure_code="WORKER_LOST", attempt_category="run_failed", retryable=True)
    numerical = FailureClassification(profile=DetectorProfile.GPU_TRAINING, enabled_detectors=["nan_or_inf"], matched_detector="nan_or_inf", failure_code="NAN_OR_INF", attempt_category="run_failed", retryable=False)
    assert RetryPolicy().should_retry(_attempt("WORKER_LOST"), retryable)
    assert not RetryPolicy().should_retry(_attempt("NAN_OR_INF"), numerical)

def test_health_diagnosis_is_advisory_and_only_for_unknown_or_conflict():
    assert HealthDiagnosisAgent().diagnose(failure_code="OOM", health_events=[]) is None
    assert HealthDiagnosisAgent().diagnose(failure_code="UNKNOWN_RUN_FAILURE", health_events=[]).verdict == "INSUFFICIENT_EVIDENCE"


def test_health_diagnosis_does_not_call_advisor_for_normal_or_known_failure():
    calls: list[str] = []
    agent = HealthDiagnosisAgent(advisor=lambda code, events: (calls.append(code), HealthDiagnosisAgent().diagnose(failure_code="UNKNOWN_RUN_FAILURE", health_events=[]))[1])
    assert agent.diagnose(failure_code=None, health_events=[]) is None
    assert agent.diagnose(failure_code="OOM", health_events=[]) is None
    assert calls == []
from autoad_researcher.experiment.failure_classifier import DetectorProfile, FailureClassification
