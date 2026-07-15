#!/usr/bin/env python3
"""Read-only verifier for Phase 2D HITL demo artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    next_action: str | None = None


def verify_hitl_artifacts(run_dir: Path) -> list[Check]:
    checks: list[Check] = []

    checks.append(_exists(run_dir / "input_task.yaml", "input_task.yaml", "generate input_task.yaml from Research Assistant"))
    checks.append(_exists(run_dir / "ui_chat" / "intent_draft.json", "intent_draft.json", "create an intent draft in Research Assistant"))
    checks.append(_exists(run_dir / "ui_chat" / "clarification_input.json", "clarification_input.json", "generate clarification input from intent draft"))

    patch_approval = _read_json(run_dir / "approvals" / "patch_approval.json")
    if patch_approval and patch_approval.get("confirmed_by_user") is True:
        checks.append(Check("PASS", "patch_approval confirmed"))
    else:
        checks.append(Check("BLOCKED", "patch_approval confirmed", "open Research Assistant -> Patch Plan Approval -> confirm"))

    patch_applicator_gate = _read_json(run_dir / "patch_applicator" / "approval_gate_report.json")
    checks.append(_gate_check(patch_applicator_gate, "patch applicator approval gate report"))

    run_approval = _read_json(run_dir / "approvals" / "run_approval.json")
    if run_approval and run_approval.get("confirmed_by_user") is True:
        checks.append(Check("PASS", "run_approval confirmed"))
    else:
        checks.append(Check("BLOCKED", "run_approval confirmed", "open Research Assistant -> Real Execution Approval -> confirm"))

    runner_gate = _read_json(run_dir / "runner_execute" / "approval_gate_report.json")
    checks.append(_gate_check(runner_gate, "runner execute approval gate report"))

    manifest = _read_json(run_dir / "runner_execute" / "execution_manifest.json")
    if _manifest_explainable(manifest):
        checks.append(Check("PASS", "execution_manifest status explainable"))
    else:
        checks.append(Check("BLOCKED", "execution_manifest status explainable", "run pipeline through runner_execute or inspect runner artifacts"))

    checks.append(_exists(run_dir / "final_report" / "final_report_facts.json", "final_report_facts.json", "run pipeline through final_report"))
    return checks


def format_report(run_id: str, checks: list[Check]) -> str:
    lines = ["HITL artifact verification", f"run_id: {run_id}", ""]
    for check in checks:
        lines.append(f"[{check.status}] {check.name}")
        if check.next_action:
            lines.append(f"next_action: {check.next_action}")
    status = "passed" if all(check.status == "PASS" for check in checks) else "blocked"
    lines.extend(["", f"status: {status}"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", default="runs")
    args = parser.parse_args(argv)

    run_dir = Path(args.runs_root) / args.run_id
    checks = verify_hitl_artifacts(run_dir)
    sys.stdout.write(format_report(args.run_id, checks))
    return 0 if all(check.status == "PASS" for check in checks) else 2


def _exists(path: Path, name: str, next_action: str) -> Check:
    if path.is_file():
        return Check("PASS", name)
    return Check("BLOCKED", name, next_action)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _gate_check(report: dict[str, Any] | None, name: str) -> Check:
    if not report:
        return Check("BLOCKED", name, "run pipeline until this gate is evaluated")
    status = report.get("status")
    if status in {"passed", "blocked"}:
        return Check("PASS", name)
    return Check("BLOCKED", name, "inspect malformed approval_gate_report.json")


def _manifest_explainable(manifest: dict[str, Any] | None) -> bool:
    if not manifest:
        return False
    if manifest.get("status") in {"completed", "blocked", "failed"}:
        return True
    for key in ("completed_unit_count", "failed_unit_count", "blocked_unit_count"):
        if key in manifest:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
