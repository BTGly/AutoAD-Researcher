"""Deterministic control-plane retry policy for infrastructure failures."""
from __future__ import annotations
from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.failure_classifier import FailureClassification

RETRYABLE_FAILURE_CODES = {"WORKER_LOST", "TEMPORARY_GPU_UNAVAILABLE", "TRANSIENT_IO_ERROR", "PROCESS_SPAWN_FAILED"}

class RetryPolicy:
    """Retry only an explicit, bounded infrastructure classification."""

    def should_retry(
        self,
        attempt: ExperimentAttempt,
        classification: FailureClassification | None,
    ) -> bool:
        return (
            classification is not None
            and classification.retryable
            and classification.failure_code in RETRYABLE_FAILURE_CODES
            and attempt.retry_count < attempt.max_retries
            and not attempt.retry_exhausted
        )
