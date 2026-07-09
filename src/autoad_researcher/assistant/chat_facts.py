"""Extract user-confirmed facts from chat transcript using rule-based matching.

Never uses LLM — deterministic keyword extraction only.
"""

from __future__ import annotations

import re
from typing import Any

from autoad_researcher.assistant.v2.need_discovery import canonicalize_metrics


def extract_confirmed_from_chat(transcript_tail: list[dict[str, Any]]) -> dict[str, Any]:
    if not transcript_tail:
        return {}

    user_turns = [
        str(entry.get("content", ""))
        for entry in transcript_tail
        if entry.get("role") == "user"
    ]
    all_text = " ".join(user_turns).lower()
    latest_user_text = _latest_substantive_user_text(user_turns).lower()
    latest_metric_focus_text = _latest_metric_focus_text(user_turns).lower()

    if not all_text.strip():
        return {}

    facts: dict[str, Any] = {}

    if re.search(r"mvtec\s*(ad)?", all_text, re.IGNORECASE):
        facts["dataset"] = "MVTec AD"

    if re.search(r"(patch\s*)?\bcore\b|pathcore|patch\s*core", all_text, re.IGNORECASE):
        facts["baseline"] = "PatchCore"

    if re.search(r"特征提取|backbone|feature\s*extract", all_text):
        facts["research_direction"] = "feature_extractor"

    metrics = canonicalize_metrics(all_text)
    latest_metrics = canonicalize_metrics(latest_user_text)
    latest_focus_metrics = canonicalize_metrics(latest_metric_focus_text)
    if _is_metric_correction(latest_metric_focus_text) and latest_focus_metrics:
        metrics = latest_focus_metrics
    elif latest_focus_metrics and _is_current_focus_statement(latest_metric_focus_text):
        metrics = latest_focus_metrics
    elif _is_metric_correction(latest_user_text) and latest_metrics:
        metrics = latest_metrics
    elif latest_metrics and _is_current_focus_statement(latest_user_text):
        metrics = latest_metrics
    if metrics:
        facts["metrics"] = metrics

    if re.search(r"越高越好", all_text):
        facts["metric_direction"] = "higher_is_better"

    m = re.search(r"(一天|24\s*(小时|h|hrs|hours?))", all_text)
    if m:
        facts["budget"] = {"per_candidate_time_limit": "24h"}

    if re.search(r"(不能|不改?|不许|禁止).*(基础框架|框架|core\s*pipeline)", all_text):
        facts["framework_constraint"] = "preserve_patchcore_core_pipeline"

    return facts


def _latest_substantive_user_text(user_turns: list[str]) -> str:
    for text in reversed(user_turns):
        cleaned = str(text).strip()
        if cleaned:
            return cleaned
    return ""


def _latest_metric_focus_text(user_turns: list[str]) -> str:
    for text in reversed(user_turns):
        cleaned = str(text).strip()
        if cleaned and (_is_metric_correction(cleaned.lower()) or _is_current_focus_statement(cleaned.lower())):
            return cleaned
    return ""


def _is_metric_correction(text: str) -> bool:
    return bool(
        any(token in text for token in ("不是", "不对", "错了", "我关注的不是", "我不是想"))
        and canonicalize_metrics(text)
    )


def _is_current_focus_statement(text: str) -> bool:
    return bool(
        any(token in text for token in ("我关注", "核心", "主要", "优先", "就是想", "想提升"))
        and canonicalize_metrics(text)
    )
