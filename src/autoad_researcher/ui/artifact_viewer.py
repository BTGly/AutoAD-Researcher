"""Read-only helpers for browsing run artifacts.

The viewer is intentionally schema-agnostic: it exposes the material and
intent-alignment artifacts that exist, without encoding the removed Stage 3
linear pipeline.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from autoad_researcher.core.run_id import run_dir_path as core_run_dir_path

BLOCKED_REASON_HINTS = {
    "blocked_missing_approval:patch_approval": "缺少实现变更审批。",
    "blocked_missing_approval:run_approval": "缺少真实执行审批。",
    "blocked_real_execution_not_allowed:run_approval": "当前环境未允许真实执行。",
}


def run_dir_path(runs_root: str, run_id: str) -> Path:
    return core_run_dir_path(runs_root, run_id)


def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_stage_dirs(run_dir: Path) -> list[dict[str, Any]]:
    if not run_dir.is_dir():
        return []
    return [
        {"name": path.name, "exists": True, "path": str(path), "description": "", "recommended": []}
        for path in sorted(run_dir.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


def list_artifact_files(run_dir: Path, stage: str) -> list[dict[str, Any]]:
    path = run_dir / stage
    if not path.is_dir() or path.resolve().parent != run_dir.resolve():
        return []
    return [
        {"name": item.name, "size": item.stat().st_size, "path": str(item.relative_to(run_dir))}
        for item in sorted(path.iterdir())
        if item.is_file()
    ]


def get_events_tail(run_dir: Path, n: int = 30) -> list[str]:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").strip().splitlines()[-n:]


def get_artifact_chain(run_dir: Path) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    for stage in list_stage_dirs(run_dir):
        handoffs = sorted(Path(stage["path"]).glob("*handoff*.json"))
        handoff = handoffs[0] if handoffs else None
        chain.append({
            "stage": stage["name"],
            "handoff_sha": _sha256_file(handoff) if handoff else "—",
            "exists": True,
        })
    return chain


def get_approval_gate_report(run_dir: Path, stage: str) -> dict[str, Any] | None:
    if not stage or Path(stage).name != stage:
        return None
    data = read_json(run_dir / stage / "approval_gate_report.json")
    return data if isinstance(data, dict) else None


def get_execution_manifest(run_dir: Path) -> dict[str, Any] | None:
    data = read_json(run_dir / "runner_execute" / "execution_manifest.json")
    return data if isinstance(data, dict) else None


def get_runner_intake_report(run_dir: Path) -> dict[str, Any] | None:
    data = read_json(run_dir / "runner_execute" / "runner_intake_report.json")
    return data if isinstance(data, dict) else None


def get_gpu_evidence(run_dir: Path) -> dict[str, Any] | None:
    data = read_json(run_dir / "runner_execute" / "gpu_execution_evidence.json")
    return data if isinstance(data, dict) else None


def get_final_facts(run_dir: Path) -> dict[str, Any] | None:
    data = read_json(run_dir / "final_report" / "final_report_facts.json")
    return data if isinstance(data, dict) else None


def get_final_report_md(run_dir: Path) -> str | None:
    path = run_dir / "final_report" / "final_report.md"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
