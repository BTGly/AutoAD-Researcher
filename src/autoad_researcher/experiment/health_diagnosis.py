"""Advisory diagnosis boundary; it never controls a process."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict

class HealthDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["HEALTHY", "LIKELY_SLOW", "LIKELY_STUCK", "LIKELY_CONFIG_ERROR", "LIKELY_NUMERICAL_FAILURE", "INSUFFICIENT_EVIDENCE"]
    evidence: list[str]

class HealthDiagnosisAgent:
    """Runs only for unknown or conflicting deterministic evidence."""
    def diagnose(self, *, failure_code: str | None, health_events: list[str]) -> HealthDiagnosis | None:
        if failure_code not in {None, "UNKNOWN_RUN_FAILURE"} and not {"OOM_DETECTED", "NAN_OR_INF"}.intersection(health_events): return None
        if "NAN_OR_INF" in health_events: return HealthDiagnosis(verdict="LIKELY_NUMERICAL_FAILURE", evidence=health_events)
        return HealthDiagnosis(verdict="INSUFFICIENT_EVIDENCE", evidence=health_events)
