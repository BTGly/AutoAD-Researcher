"""Read-only helpers for browsing runs/{run_id} artifacts."""

import hashlib
import json
from pathlib import Path
from typing import Any

from autoad_researcher.core.run_id import run_dir_path as core_run_dir_path
from autoad_researcher.schemas.stage3_acceptance import STAGE3_ACCEPTANCE_STAGE_ORDER

STAGE_NAMES = list(STAGE3_ACCEPTANCE_STAGE_ORDER) + ["stage3_acceptance"]

HANDOFF_FILES = {
    "intake": "intake_handoff.json",
    "repository_intelligence": "repository_intelligence_handoff.json",
    "paper_intelligence": "paper_intelligence_handoff.json",
    "research_context": "research_context_handoff.json",
    "transfer_design": "idea_transfer_design_handoff.json",
    "experiment_planner": "experiment_plan_handoff.json",
    "patch_planner": "patch_planner_handoff.json",
    "patch_applicator": "patch_runner_handoff.json",
    "runner_execute": "experiment_execution_handoff.json",
    "results_analysis": "results_analysis_handoff.json",
    "final_report": "final_report_handoff.json",
    "stage3_acceptance": "stage3_acceptance_result.json",
}

STAGE_DESCRIPTIONS: dict[str, str] = {
    "intake": "读入用户任务描述，校验输入完整性",
    "repository_intelligence": "分析目标代码仓库结构、依赖、入口点",
    "paper_intelligence": "解析论文内容，提取方法、指标、实验设置",
    "research_context": "将论文方法映射到目标仓库上下文",
    "transfer_design": "设计论文方法在目标仓库中的迁移方案",
    "experiment_planner": "规划 baseline 和 variant 实验矩阵",
    "patch_planner": "生成代码修改方案（尚未改代码）",
    "patch_applicator": "将修改方案变成真实 diff，应用到仓库",
    "runner_execute": "执行 baseline 和 variant 实验",
    "results_analysis": "比较 baseline/variant 实验结果",
    "final_report": "生成最终科学报告",
    "stage3_acceptance": "Stage 3 全链路验收结果",
}

RECOMMENDED_FILES: dict[str, list[str]] = {
    "patch_planner": ["patch_plan.json", "patch_planner_handoff.json"],
    "patch_applicator": ["patch_runner_handoff.json", "patch_execution_result.json"],
    "runner_execute": ["execution_manifest.json", "gpu_execution_evidence.json", "runner_intake_report.json"],
    "results_analysis": ["results_analysis_handoff.json", "reflection.json"],
    "final_report": ["final_report_facts.json", "final_report.md", "final_report_handoff.json"],
    "stage3_acceptance": ["end_to_end_run_report.json", "stage3_acceptance_result.json"],
}


def run_dir_path(runs_root: str, run_id: str) -> Path:
    return core_run_dir_path(runs_root, run_id)


def list_stage_dirs(run_dir: Path) -> list[dict[str, Any]]:
    stages = []
    for name in STAGE_NAMES:
        d = run_dir / name
        stages.append({
            "name": name,
            "exists": d.is_dir(),
            "path": str(d),
            "description": STAGE_DESCRIPTIONS.get(name, ""),
            "recommended": RECOMMENDED_FILES.get(name, []),
        })
    return stages


def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return "—"
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    """Read known handoff files from each stage and return their SHAs."""
    chain = []
    for name in STAGE_NAMES:
        handoff_name = HANDOFF_FILES.get(name)
        handoff_path = run_dir / name / handoff_name if handoff_name else None
        sha = None
        exists = (run_dir / name).is_dir()
        if handoff_path and handoff_path.is_file():
            data = read_json(handoff_path)
            if isinstance(data, dict):
                for key in ("handoff_sha256", "sha256"):
                    if key in data:
                        sha = data[key]
                        break
            if not sha:
                sha = _sha256_file(handoff_path)
        chain.append({
            "stage": name,
            "handoff_sha": sha or "—",
            "exists": exists,
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
            "pipeline_success": None,
            "execution_success": None,
            "scientific_success": None,
            "scientific_claim": None,
        }
    noop = final_facts.get("noop_patch")
    patch_valid = noop is False
    stages = final_facts.get("pipeline_stages", {})
    pipeline_ok = (
        patch_valid
        and stages.get("patch_planner") == "passed"
        and stages.get("patch_applicator") == "passed"
        and stages.get("runner_execute") == "passed"
        and stages.get("results_analysis") == "passed"
        and stages.get("final_report") == "passed"
    )
    execution = (
        final_facts.get("execution_mode") == "gpu_verified"
        and final_facts.get("l3_gpu_claim") == "completed"
    )
    if manifest:
        total = manifest.get("total_unit_count") or manifest.get("completed_unit_count", 0) + manifest.get("failed_unit_count", 0) + manifest.get("blocked_unit_count", 0)
        execution = (
            execution
            and total > 0
            and manifest.get("completed_unit_count", 0) == total
            and manifest.get("failed_unit_count", 0) == 0
        )
    scientific_claim = final_facts.get("scientific_claim")
    scientific_ok = scientific_claim in {"improvement_demonstrated", "improvement_observed", "positive", "supported"}
    return {
        "pipeline_success": pipeline_ok,
        "execution_success": execution,
        "scientific_success": scientific_ok,
        "scientific_claim": scientific_claim,
    }
