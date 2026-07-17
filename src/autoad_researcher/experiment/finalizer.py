"""The sole writer of an Attempt's outcome_card.json."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel, ConfigDict
from autoad_researcher.experiment.failure_classifier import classify_or_load

class OutcomeCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    attempt_id: str
    runtime_status: str
    attempt_category: str
    execution_result_ref: str
    health_events_ref: str | None = None
    failure_classification_ref: str | None = None
    metrics: dict[str, Any] | None = None

def finalize_attempt(attempt_dir: Path, *, attempt_id: str, runtime_status: str) -> OutcomeCard:
    path = attempt_dir / "outcome_card.json"
    if path.is_file(): return OutcomeCard.model_validate_json(path.read_text(encoding="utf-8"))
    failed = runtime_status != "COMPLETED"
    classification_ref = None
    if failed:
        classify_or_load(attempt_dir); classification_ref = "failure_classification.json"
    metrics = _metrics(attempt_dir / "metrics.json")
    card = OutcomeCard(attempt_id=attempt_id, runtime_status=runtime_status, attempt_category="run_failed" if failed else "scientifically_evaluable", execution_result_ref="execution_result.json", health_events_ref="health_events.jsonl" if (attempt_dir / "health_events.jsonl").is_file() else None, failure_classification_ref=classification_ref, metrics=metrics)
    path.write_text(json.dumps(card.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    return card
def _metrics(path: Path) -> dict[str, Any] | None:
    if not path.is_file(): return None
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError: return None
    return value if isinstance(value, dict) else None
