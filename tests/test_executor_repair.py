from __future__ import annotations
import json
from pathlib import Path
from autoad_researcher.experiment.executor_repair import RepairRecord, append_repair_record, classify_repair_failure

def test_repair_log_is_structured_and_classifies_only_deterministic_failures(tmp_path: Path):
    path = tmp_path / "repair_log.jsonl"
    append_repair_record(path, RepairRecord(repair_index=1, trigger="REPAIR_REJECTED_HARD", classification="hard_policy_violation", patch_ref="patch.diff", validation_result="forbidden path"))
    assert json.loads(path.read_text(encoding="utf-8"))["repair_index"] == 1
    assert classify_repair_failure("REPAIR_REJECTED_HARD") == "hard_policy_violation"
    assert classify_repair_failure("SEARCH_NOT_UNIQUE") == "parser_error"
