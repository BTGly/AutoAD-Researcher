"""Chinese research draft state for the right sidebar."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoad_researcher.assistant.chat_facts import extract_confirmed_from_chat
from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.intent_contract import CORE_REQUIRED_FIELDS, load_contract_draft
from autoad_researcher.ui.sources import load_source_registry


FIELD_LABELS = {
    "research_goal": "研究目标",
    "baseline": "基线方法",
    "dataset": "数据集",
    "primary_metrics": "主要指标",
    "success_criteria": "成功标准",
    "execution_mode": "执行模式",
}

METRIC_LABELS = {
    "image_level_auroc": "图像级 AUROC",
    "pixel_level_auroc": "像素级 AUROC",
    "auroc": "AUROC",
}

HINT_LABELS = {
    "feature_adapter": "特征适配器",
    "synthetic_anomaly_features": "合成异常特征",
    "discriminator_score_calibration": "判别器/分数校准",
    "feature_extractor": "特征提取器",
    "sampling": "采样/coreset 策略",
}


def load_research_draft_state(run_dir: Path) -> dict[str, Any]:
    transcript = _load_transcript(run_dir)
    ctx = build_llm_context(run_dir, transcript_tail=transcript)
    contract = load_contract_draft(run_dir)
    sources = load_source_registry(run_dir).get("sources", [])
    usable = ctx.get("usable_evidence", []) or []
    pending_jobs = ctx.get("pending_jobs", []) or []
    failed_jobs = ctx.get("failed_jobs", []) or []

    if contract is not None:
        primary_metrics = _augment_metrics_from_transcript(contract.primary_metrics, transcript)
        fields = {
            "research_goal": _display_goal(contract.research_goal, contract.baseline, contract.dataset, primary_metrics),
            "baseline": contract.baseline,
            "dataset": contract.dataset,
            "primary_metrics": primary_metrics,
            "success_criteria": _display_success_criteria(contract.success_criteria, contract.baseline, primary_metrics),
            "execution_mode": contract.execution_mode,
            "baseline_repo": contract.baseline_repo,
            "user_improvement_hints": contract.user_improvement_hints or _improvement_hints_from_transcript_and_evidence(transcript, usable),
            "preferred_method_hints": contract.preferred_method_hints or _method_hints_from_evidence(usable),
        }
        missing = list(contract.missing_required_fields)
        ready = contract.ready_for_plan
    else:
        confirmed = extract_confirmed_from_chat(transcript)
        metrics = confirmed.get("metrics") if isinstance(confirmed.get("metrics"), list) else []
        fields = {
            "research_goal": _fallback_goal(confirmed),
            "baseline": confirmed.get("baseline"),
            "dataset": confirmed.get("dataset"),
            "primary_metrics": metrics,
            "success_criteria": None,
            "execution_mode": "plan_only",
            "baseline_repo": _first_repo_source(sources),
            "user_improvement_hints": [],
            "preferred_method_hints": _method_hints_from_evidence(usable),
        }
        missing = _missing_fields(fields)
        ready = not missing

    return {
        "schema_version": 1,
        "ready": bool(ready),
        "has_draft": _has_any_draft_signal(fields, sources, usable, pending_jobs, failed_jobs),
        "title": "研究计划草案",
        "fields": _render_fields(fields),
        "missing": [{"field": item, "label": FIELD_LABELS.get(item, item)} for item in missing],
        "sources": [_source_summary(source) for source in sources if isinstance(source, dict)],
        "evidence": [_evidence_summary(item) for item in usable if isinstance(item, dict)],
        "jobs": [_job_summary(item) for item in [*pending_jobs, *failed_jobs] if isinstance(item, dict)],
        "next_questions": _next_questions(missing, fields, usable),
    }


def _load_transcript(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "chat" / "transcript.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _fallback_goal(confirmed: dict[str, Any]) -> str | None:
    baseline = confirmed.get("baseline")
    dataset = confirmed.get("dataset")
    metrics = confirmed.get("metrics")
    if baseline and dataset and metrics:
        metric_text = "、".join(_metric_label(metric) for metric in metrics)
        return f"提升 {baseline} 在 {dataset} 上的 {metric_text}"
    return None


def _augment_metrics_from_transcript(metrics: list[str], transcript: list[dict[str, Any]]) -> list[str]:
    values = list(metrics or [])
    text = "\n".join(str(entry.get("content") or "") for entry in transcript if entry.get("role") == "user")
    lowered = text.lower()
    if (
        "auroc" in lowered
        and any(token in text for token in ("两种", "两个", "主流"))
        and "image_level_auroc" in values
        and "pixel_level_auroc" not in values
    ):
        values.append("pixel_level_auroc")
    return values


def _display_goal(goal: str | None, baseline: str | None, dataset: str | None, metrics: list[str]) -> str | None:
    if goal and goal != "提升 baseline 在目标数据集上的表现":
        return goal
    if baseline and dataset and metrics:
        metric_text = "、".join(_metric_label(metric) for metric in metrics)
        return f"提升 {baseline} 在 {dataset} 上的 {metric_text}"
    return goal


def _display_success_criteria(value: str | None, baseline: str | None, metrics: list[str]) -> str | None:
    if value in (None, ""):
        return value
    metric_text = "、".join(_metric_label(metric) for metric in metrics) if metrics else "AUROC"
    if len(value) > 160 or "\n" in value or "成功标准" in value:
        target = baseline or "baseline"
        return f"{metric_text} 高于 {target} 基线（保持相同评估设置）"
    if "improve" in value and "PatchCore baseline" in value:
        return f"{metric_text} 高于 PatchCore 基线（保持相同评估设置）"
    return value


def _missing_fields(fields: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in CORE_REQUIRED_FIELDS:
        value = fields.get(field)
        if value in (None, "", [], {}):
            missing.append(field)
    return missing


def _render_fields(fields: dict[str, Any]) -> list[dict[str, Any]]:
    ordered = [
        ("research_goal", "研究目标"),
        ("baseline", "基线方法"),
        ("dataset", "数据集"),
        ("primary_metrics", "主要指标"),
        ("success_criteria", "成功标准"),
        ("execution_mode", "执行模式"),
        ("baseline_repo", "基线仓库"),
        ("preferred_method_hints", "论文/方法线索"),
        ("user_improvement_hints", "用户改进想法"),
    ]
    rendered: list[dict[str, Any]] = []
    for key, label in ordered:
        value = fields.get(key)
        if key in {"preferred_method_hints", "user_improvement_hints"} and value in (None, "", [], {}):
            continue
        rendered.append({
            "field": key,
            "label": label,
            "value": _format_value(key, value),
            "status": "missing" if value in (None, "", [], {}) else "known",
        })
    return rendered


def _format_value(key: str, value: Any) -> str:
    if value in (None, "", [], {}):
        return "待补充"
    if key == "primary_metrics" and isinstance(value, list):
        return "、".join(_metric_label(str(item)) for item in value)
    if key in {"preferred_method_hints", "user_improvement_hints"} and isinstance(value, list):
        return "；".join(_hint_label(str(item)) for item in value) if value else "待补充"
    if isinstance(value, list):
        return "；".join(str(item) for item in value) if value else "待补充"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if key == "execution_mode" and value == "plan_only":
        return "仅规划，不自动改代码/跑实验"
    return str(value)


def _metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def _hint_label(hint: str) -> str:
    return HINT_LABELS.get(hint, hint)


def _first_repo_source(sources: list[Any]) -> str | None:
    for source in sources:
        if isinstance(source, dict) and source.get("kind") == "github_repo":
            return str(source.get("user_label") or source.get("stored_path") or "") or None
    return None


def _method_hints_from_evidence(evidence: list[Any]) -> list[str]:
    hints: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "")
        hint = "SimpleNet 论文方法"
        if "SimpleNet" in summary and hint not in hints:
            hints.append(hint)
    return hints


def _improvement_hints_from_transcript_and_evidence(transcript: list[dict[str, Any]], evidence: list[Any]) -> list[str]:
    text = "\n".join(str(entry.get("content") or "") for entry in transcript if entry.get("role") == "user")
    if not any(token in text for token in ("论文内", "论文里", "论文方法", "这些想法", "都可以尝试", "都列上")):
        return []
    if "SimpleNet 论文方法" not in _method_hints_from_evidence(evidence):
        return []
    return ["feature_adapter", "synthetic_anomaly_features", "discriminator_score_calibration"]


def _source_summary(source: dict[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(source.get("source_id") or ""),
        "label": str(source.get("user_label") or source.get("stored_path") or source.get("source_id") or ""),
        "kind": str(source.get("kind") or ""),
        "status": str(source.get("status") or ""),
    }


def _evidence_summary(item: dict[str, Any]) -> dict[str, str]:
    return {
        "source_id": str(item.get("source_id") or ""),
        "type": str(item.get("evidence_type") or ""),
        "artifact_path": str(item.get("artifact_path") or ""),
        "summary": str(item.get("summary") or "")[:240],
    }


def _job_summary(job: dict[str, Any]) -> dict[str, str]:
    return {
        "job_id": str(job.get("job_id") or ""),
        "source_id": str(job.get("source_id") or ""),
        "job_type": str(job.get("job_type") or ""),
        "status": str(job.get("status") or ""),
        "error": str(job.get("error") or ""),
    }


def _next_questions(missing: list[str], fields: dict[str, Any], evidence: list[Any]) -> list[str]:
    questions: list[str] = []
    if "success_criteria" in missing:
        questions.append("成功标准是什么？例如 AUROC 至少提升多少，或超过哪个 baseline 数值。")
    if not fields.get("baseline_repo"):
        questions.append("是否需要我继续 clone/分析 PatchCore 官方仓库来定位可改模块？")
    if evidence and not fields.get("user_improvement_hints"):
        questions.append("是否采用论文中的特征适配器、合成异常特征、判别器校准等方向作为候选改进？")
    return questions[:3]


def _has_any_draft_signal(
    fields: dict[str, Any],
    sources: list[Any],
    evidence: list[Any],
    pending_jobs: list[Any],
    failed_jobs: list[Any],
) -> bool:
    return any(value not in (None, "", [], {}) for value in fields.values()) or bool(sources or evidence or pending_jobs or failed_jobs)
