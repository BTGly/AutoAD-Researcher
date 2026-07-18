"""Auditable bounded repair records for Executor patch failures."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class RepairRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repair_index: int = Field(ge=1)
    trigger: str = Field(min_length=1)
    classification: Literal["syntax_error", "import_error", "shape_error", "parser_error", "smoke_failure", "bounded_oom_adjustment", "hard_policy_violation"]
    patch_ref: str | None = None
    validation_result: str = Field(min_length=1)

def append_repair_record(path: Path, record: RepairRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
        handle.flush()

def classify_repair_failure(code: str) -> str:
    if code == "REPAIR_REJECTED_HARD": return "hard_policy_violation"
    if code == "SEARCH_NOT_UNIQUE": return "parser_error"
    if code == "ROLLBACK": return "syntax_error"
    return "smoke_failure"
