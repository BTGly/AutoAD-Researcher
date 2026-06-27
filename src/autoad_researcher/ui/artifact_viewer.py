"""Read-only helpers for browsing runs/{run_id} artifacts."""

import json
from pathlib import Path
from typing import Any

from autoad_researcher.core.run_id import run_dir_path as core_run_dir_path
from autoad_researcher.schemas.stage3_acceptance import STAGE3_ACCEPTANCE_STAGE_ORDER

STAGE_NAMES = list(STAGE3_ACCEPTANCE_STAGE_ORDER) + ["stage3_acceptance"]


def run_dir_path(runs_root: str, run_id: str) -> Path:
    """Validate and resolve runs_root/run_id."""
    return core_run_dir_path(runs_root, run_id)


def list_stage_dirs(run_dir: Path) -> list[dict[str, Any]]:
    stages = []
    for name in STAGE_NAMES:
        d = run_dir / name
        stages.append({
            "name": name,
            "exists": d.is_dir(),
            "path": str(d),
        })
    return stages


def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_execution_manifest(run_dir: Path) -> dict | None:
    return read_json(run_dir / "runner_execute" / "execution_manifest.json")


def get_runner_intake_report(run_dir: Path) -> dict | None:
    return read_json(run_dir / "runner_execute" / "runner_intake_report.json")


def get_gpu_evidence(run_dir: Path) -> dict | None:
    return read_json(run_dir / "runner_execute" / "gpu_execution_evidence.json")


def get_final_facts(run_dir: Path) -> dict | None:
    return read_json(run_dir / "final_report" / "final_report_facts.json")


def get_final_report_md(run_dir: Path) -> str | None:
    p = run_dir / "final_report" / "final_report.md"
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return None


def get_events_tail(run_dir: Path, n: int = 30) -> list[str]:
    p = run_dir / "events.jsonl"
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    return lines[-n:]


def get_artifact_chain(run_dir: Path) -> list[dict[str, str]]:
    """Read handoff.json from each stage and extract its SHA."""
    chain = []
    for name in STAGE_NAMES:
        handoff_path = run_dir / name
        candidates = list(handoff_path.glob("*handoff*.json"))
        sha = None
        if candidates:
            data = read_json(candidates[0])
            if isinstance(data, dict):
                for key in ("handoff_sha256", "sha256"):
                    if key in data:
                        sha = data[key]
                        break
        chain.append({
            "stage": name,
            "handoff_sha": sha or "—",
            "exists": handoff_path.is_dir(),
        })
    return chain


def list_artifact_files(run_dir: Path, stage: str) -> list[dict[str, Any]]:
    d = run_dir / stage
    if not d.is_dir():
        return []
    results = []
    for f in sorted(d.iterdir()):
        if f.is_file():
            size = f.stat().st_size
            results.append({
                "name": f.name,
                "size": size,
                "path": str(f.relative_to(run_dir)),
            })
    return results


def summarize_final_status(final_facts: dict | None, manifest: dict | None) -> dict[str, Any]:
    if not final_facts:
        return {
            "engineering_success": None,
            "execution_success": None,
            "scientific_success": None,
            "scientific_claim": None,
        }
    engineering = final_facts.get("noop_patch") is False
    execution = (
        final_facts.get("execution_mode") == "gpu_verified"
        and final_facts.get("l3_gpu_claim") == "completed"
    )
    if manifest:
        execution = execution and manifest.get("completed_unit_count") == 3 and manifest.get("failed_unit_count") == 0
    scientific_claim = final_facts.get("scientific_claim")
    scientific_ok = scientific_claim in {"improvement_demonstrated", "improvement_observed", "positive", "supported"}
    return {
        "engineering_success": engineering,
        "execution_success": execution,
        "scientific_success": scientific_ok,
        "scientific_claim": scientific_claim,
    }
