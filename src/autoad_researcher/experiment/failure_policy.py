"""Deterministic post-classification action policy with explicit repair authority."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.failure_classifier import FailureClassification


class FailurePolicyContext(BaseModel):
    """Caller-supplied facts; parameter names and progress are never guessed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    retry_count: int = Field(ge=0)
    max_retries: int = Field(ge=0)
    repair_count: int = Field(ge=0)
    max_repairs: int = Field(ge=0)
    oom_repair_authorized: bool = False
    timeout_progress_observed: bool = False
    timeout_extension_authorized: bool = False


class FailurePolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[
        "bounded_repair",
        "retry_infrastructure",
        "increase_timeout_retry",
        "coordinator_review",
        "reject_protocol",
        "archive_failure",
        "no_retry",
    ]
    reason: str = Field(min_length=1)
    consumes_retry: bool = False
    consumes_repair: bool = False


class FailurePolicy:
    """Map exact structured failure codes to bounded actions."""

    INFRA_RETRY_CODES = {
        "WORKER_LOST",
        "TEMPORARY_GPU_UNAVAILABLE",
        "TRANSIENT_IO_ERROR",
        "PROCESS_SPAWN_FAILED",
    }
    PROTOCOL_CODES = {"PROTECTED_ARTIFACT_CHANGED", "PROTOCOL_VIOLATED"}

    def decide(
        self,
        *,
        classification: FailureClassification,
        context: FailurePolicyContext,
    ) -> FailurePolicyDecision:
        code = classification.failure_code
        if code in self.PROTOCOL_CODES or classification.attempt_category == "protocol_violated":
            return FailurePolicyDecision(
                action="reject_protocol",
                reason="protocol violations are excluded and never retried",
            )
        if code == "OOM":
            if context.oom_repair_authorized and context.repair_count < context.max_repairs:
                return FailurePolicyDecision(
                    action="bounded_repair",
                    reason="the frozen InterventionContract explicitly authorizes an OOM repair",
                    consumes_repair=True,
                )
            return FailurePolicyDecision(
                action="no_retry",
                reason="OOM has no explicit bounded repair authority",
            )
        if code == "NAN_OR_INF":
            return FailurePolicyDecision(
                action="coordinator_review",
                reason="NaN/Inf is not automatically classified as infrastructure failure",
            )
        if code == "RUN_TIMEOUT":
            if (
                context.timeout_progress_observed
                and context.timeout_extension_authorized
                and context.retry_count < min(context.max_retries, 1)
            ):
                return FailurePolicyDecision(
                    action="increase_timeout_retry",
                    reason="structured progress evidence permits one bounded timeout extension",
                    consumes_retry=True,
                )
            return FailurePolicyDecision(
                action="no_retry",
                reason="timeout without authorized progress-based extension is terminal",
            )
        if code in self.INFRA_RETRY_CODES:
            if classification.retryable and context.retry_count < context.max_retries:
                return FailurePolicyDecision(
                    action="retry_infrastructure",
                    reason="explicit retryable infrastructure classification remains within the retry cap",
                    consumes_retry=True,
                )
            return FailurePolicyDecision(
                action="archive_failure",
                reason="infrastructure retry budget is exhausted",
            )
        if code in {"USER_CANCELLED", "DISK_FULL", "CHECKPOINT_STALLED", "METRICS_MISSING"}:
            return FailurePolicyDecision(
                action="archive_failure",
                reason="deterministic terminal failure is not automatically retried",
            )
        return FailurePolicyDecision(
            action="coordinator_review",
            reason="unknown or domain-specific failure requires structured review",
        )
