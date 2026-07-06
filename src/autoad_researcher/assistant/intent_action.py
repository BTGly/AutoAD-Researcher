"""Intent-alignment action adapter for Research Chat.

This module is deliberately small: it reads existing UI/source/artifact files,
derives a lightweight snapshot, turns the latest user message into a coarse
IntentSignal, and resolves a deterministic ActionDecision. It does not mutate
core schemas, does not call an LLM, and does not execute pipeline work.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.probe import silent_probe
from autoad_researcher.ui.intent_draft import load_intent_confirmation, load_intent_draft
from autoad_researcher.ui.sources import load_source_registry


SourceDerivedStatus = Literal[
    "reference_identifier",
    "uploaded_not_parsed",
    "parsing_in_progress",
    "parsed",
    "parsing_failed",
]
PaperArtifactQuality = Literal["missing", "usable", "insufficient"]
SelectedAction = Literal[
    "answer_directly",
    "parse_uploaded_pdf",
    "summarize_parsed_artifacts",
    "refresh_research_context_snapshot",
    "update_intent_draft",
    "ask_blocking_gap",
    "confirm_research_task",
    "block_execution_request",
]
ResponseMode = Literal[
    "empty_run_intake",
    "reference_only_status",
    "uploaded_not_parsed_status",
    "uploaded_status_then_auto_parse",
    "material_auto_parse_started",
    "select_pdf_to_parse",
    "parsing_in_progress_status",
    "parsing_failed_status",
    "parsed_artifact_summary",
    "parsed_artifact_insufficient",
    "execution_request_blocked",
    "research_task_confirmed",
    "answer_directly",
]
ExecutionStatus = Literal[
    "planned",
    "executed_success",
    "executed_failed",
    "skipped_by_idempotency",
    "blocked_by_policy",
    "needs_user_input",
]

ACTION_DECISIONS_DIR = "ui_chat"
ACTION_DECISIONS_FILE = "action_decisions.jsonl"
SNAPSHOT_FILE = "research_context_snapshot.json"


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: str
    user_label: str
    source_status: str
    derived_status: SourceDerivedStatus
    stored_path: str | None = None
    reference_value: str | None = None
    error_message: str | None = None


class ResearchContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    sources: list[SourceSnapshot] = Field(default_factory=list)
    has_reference_identifier: bool = False
    has_ingested_source: bool = False
    has_repo_evidence: bool = False
    has_parsed_artifact: bool = False
    paper_artifact_quality: PaperArtifactQuality = "missing"
    paper_artifact_warnings: list[str] = Field(default_factory=list)
    paper_methods: list[str] = Field(default_factory=list)
    paper_artifact_refs: list[str] = Field(default_factory=list)
    missing_blocking_gaps: list[str] = Field(default_factory=list)
    intent_draft_exists: bool = False
    task_confirmed: bool = False
    ready_for_pipeline: bool = False
    execution_approved: bool = False
    patch_approved: bool = False
    run_approved: bool = False


class IntentSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions_paper: bool = False
    mentions_repo: bool = False
    asks_for_paper_content: bool = False
    asks_source_status: bool = False
    requests_execution: bool = False
    ambiguous_reproduction_transfer: bool = False
    confirms_research_task: bool = False
    ready_for_task_draft: bool = False
    force_reparse: bool = False


class ActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    user_message_id: str | None = None
    snapshot_sha256: str
    selected_action: SelectedAction
    response_mode: ResponseMode
    needs_user_confirmation: bool = False
    reason: str
    execution_status: ExecutionStatus = "planned"
    source_id: str | None = None
    stored_path: str | None = None
    source_status_before: str | None = None
    source_status_after: str | None = None
    blocked_reason: str | None = None
    error_code: str | None = None
    user_visible_message: str | None = None
    user_visible_error: str | None = None


def build_research_context_snapshot(run_dir: Path) -> ResearchContextSnapshot:
    """Build a read-only adapter snapshot from existing UI/artifact files."""
    registry = _load_registry_or_empty(run_dir)
    what = _probe_or_empty(run_dir)
    sources = [_source_snapshot(source) for source in registry.get("sources", []) if isinstance(source, dict)]
    quality, warnings = evaluate_paper_artifact_quality(run_dir)
    confirmation = load_intent_confirmation(run_dir)

    snapshot = ResearchContextSnapshot(
        run_id=run_dir.name,
        sources=sources,
        has_reference_identifier=any(source.derived_status == "reference_identifier" for source in sources),
        has_ingested_source=any(source.kind in {"paper_pdf", "text", "markdown"} for source in sources),
        has_repo_evidence=what.has_repo_summary,
        has_parsed_artifact=quality == "usable",
        paper_artifact_quality=quality,
        paper_artifact_warnings=warnings,
        paper_methods=what.paper_methods if quality == "usable" else [],
        paper_artifact_refs=[ref for ref in what.evidence_artifacts if ref.startswith("paper/")],
        missing_blocking_gaps=_select_blocking_gaps(what.missing_fields),
        intent_draft_exists=load_intent_draft(run_dir) is not None,
        task_confirmed=bool(confirmation and confirmation.decision == "approved"),
        ready_for_pipeline=bool(confirmation and confirmation.decision == "approved"),
        execution_approved=False,
        patch_approved=False,
        run_approved=False,
    )
    _write_snapshot(run_dir, snapshot)
    return snapshot


def infer_intent_signal(user_message: str, snapshot: ResearchContextSnapshot) -> IntentSignal:
    """Infer coarse intent signals without enumerating every user phrase."""
    text = user_message.strip()
    lowered = text.lower()
    normalized = re.sub(r"\s+", "", lowered)
    has_paper_source = any(source.kind == "paper_pdf" for source in snapshot.sources)
    source_backed_paper_hint = has_paper_source and (
        any(token in text for token in ("读", "解析", "提取", "看看", "看一下", "分析", "基于", "内容", "artifacts", "正文", "能看到", "状态"))
        or normalized in {"对", "对啊", "是", "是的", "好", "好的", "可以", "开始", "读吧", "解析吧"}
    )
    mentions_paper = bool(
        re.search(r"\b(pdf|paper|arxiv)\b", lowered, re.IGNORECASE)
        or any(token in text for token in ("论文", "材料", "文献", "这篇"))
        or source_backed_paper_hint
    )
    mentions_repo = bool(
        "github" in lowered
        or "repo" in lowered
        or "repository" in lowered
        or "仓库" in text
        or any(source.kind in {"github_repo", "url"} and "github" in (source.reference_value or source.user_label).lower() for source in snapshot.sources)
    )
    asks_source_status = bool(
        any(token in text for token in ("能看到", "看得到", "有没有", "上传了吗", "状态", "现在能"))
        and mentions_paper
    )
    asks_for_paper_content = bool(
        mentions_paper
        and (
            any(token in text for token in ("看看", "看一下", "读", "解析", "提取", "分析", "基于", "内容", "artifacts", "正文", "复现", "迁移", "用到"))
            or bool(re.search(r"sources/[^ \t\r\n]+\.pdf", text, re.IGNORECASE))
        )
    )
    requests_execution = any(token in text for token in ("直接改代码", "改代码", "跑实验", "运行实验", "开始执行", "执行 pipeline", "benchmark"))
    ambiguous = "复现" in text and any(token in text for token in ("用到", "用在", "迁移", "我的项目", "项目里"))
    confirms = any(token in normalized for token in ("确认这个研究目标", "研究目标确认", "草案确认", "我确认", "确认草案"))
    force_reparse = any(token in normalized for token in ("重新解析", "重新提取", "重新读", "再解析", "再提取", "再读", "提取一次", "解析一次"))
    return IntentSignal(
        mentions_paper=mentions_paper,
        mentions_repo=mentions_repo,
        asks_for_paper_content=asks_for_paper_content or force_reparse,
        asks_source_status=asks_source_status,
        requests_execution=requests_execution,
        ambiguous_reproduction_transfer=ambiguous,
        confirms_research_task=confirms,
        ready_for_task_draft=_ready_for_task_draft(snapshot),
        force_reparse=force_reparse,
    )


def resolve_material_auto_action(
    *,
    snapshot: ResearchContextSnapshot,
    signal: IntentSignal,
    explicit_stored_path: str | None = None,
    recent_sources: list[dict[str, Any]] | None = None,
) -> ActionDecision:
    """Resolve an IntentSignal + snapshot to one deterministic action."""
    digest = snapshot_sha256(snapshot)

    if signal.requests_execution:
        return _decision(
            snapshot_sha256=digest,
            selected_action="block_execution_request",
            response_mode="execution_request_blocked",
            reason="user requested code modification, benchmark, or experiment execution",
            execution_status="blocked_by_policy",
            user_visible_message="当前还没有代码修改或实验执行批准。我可以先整理研究目标草案。",
        )

    target = _find_explicit_source(snapshot, explicit_stored_path)
    recent_pdf_ids = {str(source.get("source_id")) for source in (recent_sources or []) if source.get("kind") == "paper_pdf"}
    pending = _sources_with(snapshot, "uploaded_not_parsed")
    parsing = _sources_with(snapshot, "parsing_in_progress")
    parsed = _sources_with(snapshot, "parsed")
    failed = _sources_with(snapshot, "parsing_failed")

    if target is not None:
        return _decision_for_source(digest, target, signal)

    recent_pending = [source for source in pending if source.source_id in recent_pdf_ids]
    if len(recent_pending) == 1 and signal.mentions_paper:
        return _parse_decision(digest, recent_pending[0], signal, "recent uploaded PDF and user mentions paper")
    if len(recent_pending) > 1 and signal.mentions_paper:
        return _choose_pdf_decision(digest, recent_pending, "multiple recently uploaded PDFs")

    if parsing and signal.mentions_paper:
        return _decision(
            snapshot_sha256=digest,
            selected_action="answer_directly",
            response_mode="parsing_in_progress_status",
            reason="paper source is already parsing",
            execution_status="skipped_by_idempotency",
            source_id=parsing[0].source_id,
            stored_path=parsing[0].stored_path,
            source_status_before=parsing[0].derived_status,
            source_status_after=parsing[0].derived_status,
            user_visible_message="论文正在解析中，请等待完成后再继续。",
        )

    if signal.force_reparse and len(parsed) == 1:
        return _parse_decision(digest, parsed[0], signal, "user explicitly requested reparse")
    if signal.force_reparse and len(failed) == 1:
        return _parse_decision(digest, failed[0], signal, "user explicitly requested retry after parse failure")
    if signal.force_reparse and len(parsed) + len(failed) > 1:
        return _choose_pdf_decision(digest, [*parsed, *failed], "multiple parsed or failed PDFs need explicit selection")

    if parsed and signal.asks_for_paper_content:
        mode: ResponseMode = "parsed_artifact_summary" if snapshot.paper_artifact_quality == "usable" else "parsed_artifact_insufficient"
        return _decision(
            snapshot_sha256=digest,
            selected_action="summarize_parsed_artifacts",
            response_mode=mode,
            reason="parsed PDF artifacts exist",
            execution_status="skipped_by_idempotency",
            source_id=parsed[0].source_id,
            stored_path=parsed[0].stored_path,
            source_status_before=parsed[0].derived_status,
            source_status_after=parsed[0].derived_status,
        )

    if len(pending) == 1 and (signal.asks_for_paper_content or signal.asks_source_status or signal.mentions_paper):
        return _parse_decision(digest, pending[0], signal, "unique uploaded_not_parsed PDF and user mentions paper")
    if len(pending) > 1 and signal.mentions_paper:
        return _choose_pdf_decision(digest, pending, "multiple uploaded_not_parsed PDFs")

    if failed and signal.mentions_paper:
        return _decision(
            snapshot_sha256=digest,
            selected_action="answer_directly",
            response_mode="parsing_failed_status",
            reason="last parse attempt failed and user did not explicitly request retry",
            execution_status="skipped_by_idempotency",
            source_id=failed[0].source_id,
            stored_path=failed[0].stored_path,
            source_status_before=failed[0].derived_status,
            source_status_after=failed[0].derived_status,
            user_visible_message="上次论文解析没有成功。当前仍不能基于正文回答；如需重试，请明确说重新解析。",
        )

    if signal.confirms_research_task:
        return _decision(
            snapshot_sha256=digest,
            selected_action="confirm_research_task",
            response_mode="research_task_confirmed",
            reason="user confirmed research task draft",
            execution_status="planned",
            user_visible_message="研究任务边界已确认，但这不代表已经批准代码修改或实验执行。",
        )

    if snapshot.has_reference_identifier or signal.mentions_repo:
        return _decision(
            snapshot_sha256=digest,
            selected_action="answer_directly",
            response_mode="reference_only_status",
            reason="only reference identifiers are available",
            execution_status="skipped_by_idempotency",
        )

    if signal.mentions_paper:
        return _decision(
            snapshot_sha256=digest,
            selected_action="answer_directly",
            response_mode="uploaded_not_parsed_status",
            reason="paper mentioned but no uploaded PDF is available",
            execution_status="needs_user_input",
            user_visible_message="当前没有可解析的 PDF。请先上传 PDF，或提供当前任务 sources 下的 PDF。",
        )

    return _decision(
        snapshot_sha256=digest,
        selected_action="answer_directly",
        response_mode="empty_run_intake",
        reason="no material action needed",
        execution_status="skipped_by_idempotency",
    )


def append_action_decision(run_dir: Path, decision: ActionDecision, *, user_message_id: str | None = None) -> Path:
    payload = decision.model_copy(update={"user_message_id": user_message_id}).model_dump(mode="json")
    path = run_dir / ACTION_DECISIONS_DIR / ACTION_DECISIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return path


def render_response_for_decision(snapshot: ResearchContextSnapshot, decision: ActionDecision) -> str:
    """Render deterministic user-facing text for non-LLM response modes."""
    if decision.user_visible_message:
        return decision.user_visible_message
    if decision.response_mode == "empty_run_intake":
        return "当前只知道你在探索异常检测方向。你可以先上传论文 PDF、提供仓库引用，或用一句话描述目标。"
    if decision.response_mode == "reference_only_status":
        return (
            "我看到你提供了引用标识，但系统尚未摄入或解析对应材料。"
            "当前不能确认论文正文或仓库代码；如果是论文，请上传 PDF。"
        )
    if decision.response_mode == "uploaded_not_parsed_status":
        return "我看到 PDF 已进入当前任务，但尚未解析。解析完成前不能基于论文正文回答。"
    if decision.response_mode == "parsing_in_progress_status":
        return "论文正在解析中，请等待完成后再继续。"
    if decision.response_mode == "parsing_failed_status":
        return "上次论文解析没有成功。当前仍不能基于论文正文回答；你可以重新上传 PDF 或明确要求重新解析。"
    if decision.response_mode == "parsed_artifact_insufficient":
        warnings = "、".join(snapshot.paper_artifact_warnings) or "paper artifacts 证据不足"
        return f"已生成 paper artifacts，但质量不足（{warnings}）。当前不能基于论文正文作可靠判断。"
    if decision.response_mode == "parsed_artifact_summary":
        methods = "；".join(snapshot.paper_methods[:5]) if snapshot.paper_methods else "artifacts 中未看到结构化方法摘要"
        parts = [f"我会只基于已生成 artifacts 回答。当前从 artifacts 看到：{methods}"]
        if snapshot.missing_blocking_gaps:
            gaps = "、".join(snapshot.missing_blocking_gaps[:5])
            parts.append(f"仍缺：{gaps}")
        return "\n\n".join(parts)
    if decision.response_mode == "execution_request_blocked":
        return "当前还没有代码修改或实验执行批准。我可以先整理研究目标草案；这不会启动 patch、benchmark 或真实实验。"
    if decision.response_mode == "research_task_confirmed":
        return "研究任务边界已确认，但这不代表已经批准代码修改或实验执行。"
    return "我先基于当前材料状态整理候选理解；不启动代码修改或实验执行。"


def snapshot_sha256(snapshot: ResearchContextSnapshot) -> str:
    payload = snapshot.model_dump_json(exclude={"run_id"}, by_alias=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_paper_artifact_quality(run_dir: Path) -> tuple[PaperArtifactQuality, list[str]]:
    artifacts_dir = run_dir / "paper" / "artifacts"
    summary_path = artifacts_dir / "paper_summary.json"
    sources_path = artifacts_dir / "paper_idea_sources.json"
    components_path = artifacts_dir / "method_components.json"
    candidates_path = artifacts_dir / "paper_candidates.json"
    if not any(path.is_file() for path in (summary_path, sources_path, components_path, candidates_path)):
        return "missing", []

    warnings: list[str] = []
    extracted: list[str] = []
    summary = _load_json(summary_path)
    if isinstance(summary, dict):
        title = _claim_text(summary.get("title"))
        if title and _looks_garbled(title):
            warnings.append("paper_title_looks_garbled")
        for key in (
            "research_problem",
            "proposed_method",
            "core_components",
            "training_objective",
            "data_assumptions",
            "label_assumptions",
            "inference_procedure",
            "contributions",
            "stated_limitations",
            "potential_transfer_points",
        ):
            extracted.extend(_claim_text(item) for item in _iter_dicts(summary.get(key)))
    for path in (sources_path, components_path, candidates_path):
        payload = _load_json(path)
        if isinstance(payload, list) and payload:
            extracted.append(path.name)
        elif isinstance(payload, dict) and payload:
            extracted.append(path.name)
    if any(value and not _looks_garbled(value) for value in extracted):
        return "usable", warnings
    warnings.append("paper_artifacts_exist_but_no_extractable_claims")
    return "insufficient", _dedupe(warnings)


def _source_snapshot(source: dict[str, Any]) -> SourceSnapshot:
    status = _clean_str(source.get("status")) or "user_provided_not_ingested"
    kind = _clean_str(source.get("kind")) or "url"
    return SourceSnapshot(
        source_id=_clean_str(source.get("source_id")) or "unknown_source",
        kind=kind,
        user_label=_clean_str(source.get("user_label")) or _clean_str(source.get("stored_path")) or "unknown",
        source_status=status,
        derived_status=_derive_status(kind, status),
        stored_path=_clean_str(source.get("stored_path")),
        reference_value=_clean_str(source.get("reference_value")),
        error_message=_clean_str(source.get("error_message")),
    )


def _derive_status(kind: str, status: str) -> SourceDerivedStatus:
    if status == "parsing":
        return "parsing_in_progress"
    if status == "failed":
        return "parsing_failed"
    if status == "parsed":
        return "parsed"
    if status == "uploaded_not_parsed":
        return "uploaded_not_parsed"
    if kind in {"arxiv_id", "doi", "url", "github_repo"}:
        return "reference_identifier"
    return "reference_identifier"


def _decision_for_source(digest: str, source: SourceSnapshot, signal: IntentSignal) -> ActionDecision:
    if source.derived_status == "parsing_in_progress":
        return _decision(
            snapshot_sha256=digest,
            selected_action="answer_directly",
            response_mode="parsing_in_progress_status",
            reason="explicit source is already parsing",
            execution_status="skipped_by_idempotency",
            source_id=source.source_id,
            stored_path=source.stored_path,
            source_status_before=source.derived_status,
            source_status_after=source.derived_status,
        )
    if source.derived_status == "parsed" and not signal.force_reparse:
        return _decision(
            snapshot_sha256=digest,
            selected_action="summarize_parsed_artifacts",
            response_mode="parsed_artifact_summary",
            reason="explicit source is already parsed",
            execution_status="skipped_by_idempotency",
            source_id=source.source_id,
            stored_path=source.stored_path,
            source_status_before=source.derived_status,
            source_status_after=source.derived_status,
        )
    if source.derived_status == "parsing_failed" and not signal.force_reparse:
        return _decision(
            snapshot_sha256=digest,
            selected_action="answer_directly",
            response_mode="parsing_failed_status",
            reason="explicit source previously failed parsing",
            execution_status="skipped_by_idempotency",
            source_id=source.source_id,
            stored_path=source.stored_path,
            source_status_before=source.derived_status,
            source_status_after=source.derived_status,
        )
    return _parse_decision(digest, source, signal, "explicit source path selected")


def _parse_decision(digest: str, source: SourceSnapshot, signal: IntentSignal, reason: str) -> ActionDecision:
    response_mode: ResponseMode = "uploaded_status_then_auto_parse" if signal.asks_source_status else "material_auto_parse_started"
    return _decision(
        snapshot_sha256=digest,
        selected_action="parse_uploaded_pdf",
        response_mode=response_mode,
        reason=reason,
        execution_status="planned",
        source_id=source.source_id,
        stored_path=source.stored_path,
        source_status_before=source.derived_status,
        source_status_after="parsing_in_progress",
        user_visible_message=(
            "我能看到 PDF 已上传但尚未解析。我会先解析它，完成后再基于 artifacts 回答。"
            if response_mode == "uploaded_status_then_auto_parse"
            else "我看到你上传了论文 PDF。我会先解析它，再基于解析结果整理研究目标草案。"
        ),
    )


def _choose_pdf_decision(digest: str, sources: list[SourceSnapshot], reason: str) -> ActionDecision:
    names = "、".join(source.user_label for source in sources)
    return _decision(
        snapshot_sha256=digest,
        selected_action="ask_blocking_gap",
        response_mode="select_pdf_to_parse",
        reason=reason,
        needs_user_confirmation=True,
        execution_status="needs_user_input",
        user_visible_message=f"我看到多个 PDF：{names}。你想先解析哪一个？",
    )


def _decision(**kwargs: Any) -> ActionDecision:
    return ActionDecision(**kwargs)


def _sources_with(snapshot: ResearchContextSnapshot, status: SourceDerivedStatus) -> list[SourceSnapshot]:
    return [source for source in snapshot.sources if source.kind == "paper_pdf" and source.derived_status == status]


def _find_explicit_source(snapshot: ResearchContextSnapshot, stored_path: str | None) -> SourceSnapshot | None:
    if not stored_path:
        return None
    for source in snapshot.sources:
        if source.stored_path == stored_path:
            return source
    return None


def _ready_for_task_draft(snapshot: ResearchContextSnapshot) -> bool:
    required_absent = {
        "baseline_method",
        "dataset",
        "primary_metric",
        "metric_direction",
    }
    if any(field in snapshot.missing_blocking_gaps for field in required_absent):
        return False
    if snapshot.missing_blocking_gaps:
        return False
    if not snapshot.has_parsed_artifact:
        return False
    return True


def _select_blocking_gaps(missing_fields: list[str]) -> list[str]:
    priority = ["category", "metric_direction", "dataset", "primary_metric", "baseline_method"]
    selected: list[str] = []
    for field in [*priority, *missing_fields]:
        if field in missing_fields and field not in selected:
            selected.append(field)
        if len(selected) == 3:
            break
    return selected


def _probe_or_empty(run_dir: Path):
    try:
        return silent_probe(run_dir.name, runs_root=run_dir.parent)
    except Exception:
        from autoad_researcher.assistant.probe import WhatWeKnow

        return WhatWeKnow(run_id=run_dir.name)


def _load_registry_or_empty(run_dir: Path) -> dict[str, Any]:
    try:
        return load_source_registry(run_dir)
    except Exception:
        return {"schema_version": 1, "sources": []}


def _write_snapshot(run_dir: Path, snapshot: ResearchContextSnapshot) -> None:
    path = run_dir / ACTION_DECISIONS_DIR / SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _claim_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "value", "label", "mechanism_summary", "rationale_summary"):
            candidate = _clean_str(value.get(key))
            if candidate:
                return candidate
    if isinstance(value, str):
        return value.strip()
    return ""


def _iter_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _looks_garbled(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 12:
        return False
    meaningful = sum(1 for ch in stripped if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return meaningful / max(len(stripped), 1) < 0.35


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
