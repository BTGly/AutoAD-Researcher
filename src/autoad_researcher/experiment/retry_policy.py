"""Deterministic control-plane retry policy for infrastructure failures."""
from __future__ import annotations
from autoad_researcher.experiment.attempt import ExperimentAttempt

RETRYABLE_FAILURE_CODES = {"WORKER_LOST", "TEMPORARY_GPU_UNAVAILABLE", "TRANSIENT_IO_ERROR", "PROCESS_SPAWN_FAILED"}

class RetryPolicy:
    def should_retry(self, attempt: ExperimentAttempt) -> bool:
        return attempt.failure_code in RETRYABLE_FAILURE_CODES and attempt.retry_count < attempt.max_retries and not attempt.retry_exhausted
