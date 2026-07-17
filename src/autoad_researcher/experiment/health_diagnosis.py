"""Advisory diagnosis boundary; it never controls a process."""
from __future__ import annotations
from collections.abc import Callable
from typing import Literal
from pydantic import BaseModel, ConfigDict

class HealthDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["HEALTHY", "LIKELY_SLOW", "LIKELY_STUCK", "LIKELY_CONFIG_ERROR", "LIKELY_NUMERICAL_FAILURE", "INSUFFICIENT_EVIDENCE"]
    evidence: list[str]

class HealthDiagnosisAgent:
    """Runs only for unknown or conflicting deterministic evidence."""

    def __init__(self, advisor: Callable[[str, list[str]], HealthDiagnosis] | None = None):
        self._advisor = advisor

    def diagnose(
        self,
        *,
        failure_code: str | None,
        health_events: list[str],
        evidence_conflict: bool = False,
    ) -> HealthDiagnosis | None:
        if failure_code != "UNKNOWN_RUN_FAILURE" and not evidence_conflict:
            return None
        if self._advisor is not None:
            return self._advisor(failure_code or "UNKNOWN_RUN_FAILURE", health_events)
        return HealthDiagnosis(verdict="INSUFFICIENT_EVIDENCE", evidence=health_events)
