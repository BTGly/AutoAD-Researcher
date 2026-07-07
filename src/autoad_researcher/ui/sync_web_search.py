"""Synchronous Research Chat web_search bridge.

This module only surfaces candidate sources. Search results are not evidence
until a later acquisition/fetch stage attests them.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from autoad_researcher.tools.providers import RecordedWebSearchProvider, WebSearchResult
from autoad_researcher.ui.chat_transcript import redact_secrets


SYNC_SEARCH_DIR = "ui_chat"
SYNC_SEARCH_FILE = "sync_web_search_results.jsonl"
SYNC_SEARCH_STAGE = "candidate_source_only"

_MOCK_RESULTS: list[dict[str, str]] = [
    {
        "title": "DINOv2 + PatchCore for MVTec AD Anomaly Detection — GitHub",
        "url": "https://github.com/amazon-science/patchcore-inspection",
        "snippet": "Official PatchCore repository. For feature extractor improvements, DINOv2 backbone integration has been explored in community forks.",
    },
    {
        "title": "EfficientAD: Accurate Visual Anomaly Detection at Millisecond-Level Latencies — arXiv",
        "url": "https://arxiv.org/abs/2303.05165",
        "snippet": "EfficientAD proposes a lightweight feature extractor architecture for anomaly detection on MVTec AD. Compatible with PatchCore-like memory bank approaches.",
    },
    {
        "title": "Anomalib: Deep Learning Library for Anomaly Detection — GitHub",
        "url": "https://github.com/openvinotoolkit/anomalib",
        "snippet": "Anomalib provides implementations of PatchCore, PaDiM, and other methods on MVTec AD. Includes feature extractor configuration options.",
    },
    {
        "title": "Towards Total Recall in Industrial Anomaly Detection (PatchCore Paper) — arXiv",
        "url": "https://arxiv.org/abs/2106.08265",
        "snippet": "The original PatchCore paper. Describes the coreset sampling and nearest-neighbor scoring architecture.",
    },
    {
        "title": "FastRecon: Few-shot Industrial Anomaly Detection via Fast Feature Reconstruction — arXiv",
        "url": "https://arxiv.org/abs/2304.05189",
        "snippet": "A recent method for few-shot anomaly detection on MVTec AD that can be combined with PatchCore's scoring strategy.",
    },
]


class WebSearchProvider(Protocol):
    def search(self, query: str) -> list[WebSearchResult]:
        ...


def detect_sync_web_search_intent(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("web_search", "web search", "github 实现", "github实现")):
        return True
    return any(
        token in text
        for token in (
            "搜索论文",
            "搜索方法",
            "搜索 MVTec",
            "搜索MVTec",
            "最新方法",
            "找代码",
            "找论文",
            "找方法",
            "网络上搜索",
            "网上搜索",
        )
    )


class MockWebSearchProvider:
    """Fallback search provider when no real provider is configured.

    Returns curated candidate sources so the subagent inbox pipeline
    is testable end-to-end. Every result is candidate_source_only.
    """

    def search(self, query: str) -> list[WebSearchResult]:
        return [WebSearchResult.model_validate(item) for item in _MOCK_RESULTS]


def load_sync_web_search_provider() -> WebSearchProvider | None:
    fixture_path = os.environ.get("AUTOAD_RESEARCH_CHAT_WEB_SEARCH_FIXTURE")
    if not fixture_path:
        return MockWebSearchProvider()
    path = Path(fixture_path)
    if not path.is_file():
        return MockWebSearchProvider()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return MockWebSearchProvider()
    records: dict[str, list[WebSearchResult]] = {}
    for query, values in payload.items():
        if not isinstance(query, str) or not isinstance(values, list):
            continue
        parsed: list[WebSearchResult] = []
        for item in values:
            if isinstance(item, dict):
                parsed.append(WebSearchResult.model_validate(item))
        records[query] = parsed
    if not records:
        return MockWebSearchProvider()
    return RecordedWebSearchProvider(records)


def execute_sync_web_search(
    run_dir: Path,
    *,
    query: str,
    provider: WebSearchProvider | None = None,
    max_results: int = 5,
) -> dict[str, Any]:
    search_provider = provider or load_sync_web_search_provider()
    redacted_query = redact_secrets(query.strip())
    if search_provider is None:
        return {
            "status": "search_unavailable",
            "query": redacted_query,
            "stage": SYNC_SEARCH_STAGE,
            "results": [],
            "reason": "web_search provider is not configured",
        }
    try:
        results = search_provider.search(query.strip())[:max_results]
    except Exception as exc:
        return {
            "status": "search_unavailable",
            "query": redacted_query,
            "stage": SYNC_SEARCH_STAGE,
            "results": [],
            "reason": str(exc)[:200],
        }

    payload = {
        "status": "ok" if results else "no_results",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "query": redacted_query,
        "stage": SYNC_SEARCH_STAGE,
        "results": [
            {
                **result.model_dump(mode="json"),
                "source_status": SYNC_SEARCH_STAGE,
                "evidence_status": "not_evidence_until_fetched",
            }
            for result in results
        ],
    }
    _append_sync_search_record(run_dir, payload)
    return payload


def build_sync_web_search_reply(result: dict[str, Any]) -> str:
    status = str(result.get("status", "search_unavailable"))
    if status == "search_unavailable":
        return (
            "search_unavailable：当前 Research Chat 没有配置可用的 web_search provider，因此没有执行网络搜索。\n"
            "这不是后台任务；后续 discovery/acquisition 阶段仍可使用 web_search/web_fetch/git_clone 产出 artifacts。"
        )
    results = result.get("results")
    if not isinstance(results, list) or not results:
        return (
            "已同步执行 web_search，但没有返回候选来源。\n"
            "这些搜索结果只会作为 candidate_source_only，不构成论文或代码证据。"
        )
    lines = [
        "已同步执行 web_search，返回以下候选来源（candidate_source_only，不是已验证证据）："
    ]
    for index, item in enumerate(results[:5], start=1):
        if not isinstance(item, dict):
            continue
        title = _compact_line(item.get("title"))
        url = _compact_line(item.get("url"))
        snippet = _compact_line(item.get("snippet"), limit=120)
        lines.append(f"{index}. {title} — {url}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


def _append_sync_search_record(run_dir: Path, payload: dict[str, Any]) -> None:
    path = run_dir / SYNC_SEARCH_DIR / SYNC_SEARCH_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _compact_line(value: Any, *, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
