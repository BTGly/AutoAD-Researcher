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

    all_text = " ".join(
        str(entry.get("content", ""))
        for entry in transcript_tail
        if entry.get("role") == "user"
    ).lower()

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
