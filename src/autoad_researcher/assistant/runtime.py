"""AutoAD Assistant Runtime — manual Alpha conversation CLI + deterministic skeleton.

CLI usage:
  uv run autoad assistant-alpha --run-id run_alpha_001 [--provider-url URL]

Deterministic runtime for testing (Round 4):
  DeterministicAssistantRuntime — fake backend that probes artifacts
  route_user_text — coarse envelope router
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1
from autoad_researcher.assistant.events import AssistantEvent, AssistantEventType
from autoad_researcher.assistant.probe import silent_probe
from autoad_researcher.assistant.prompt_registry import get_default_prompt_registry
from autoad_researcher.assistant.prompt_selector import select_prompt_id
from autoad_researcher.assistant.session import (
    AutoADAssistantSession,
    TaskControlState,
)
from autoad_researcher.assistant.session_store import (
    AssistantTransitionRecord,
    SessionStore,
)
from autoad_researcher.assistant.task_artifacts import (
    AssistantTaskArtifactService,
)
from autoad_researcher.assistant.transition_policy import apply, validate
from autoad_researcher.core.run_id import validate_run_id


# ─────────────────────────────────────────────────────────
# Deterministic Runtime (Round 4 — fake backend for tests)
# ─────────────────────────────────────────────────────────


def route_user_text(text: str) -> AssistantEvent:
    """Coarse envelope router — NOT behavior enumeration."""
    import re

    text_lower = text.strip().lower()
    event_counter = int(datetime.now(timezone.utc).timestamp() * 1000) % 10000

    if not text.strip():
        return AssistantEvent(
            event_id=f"ev_unknown_{event_counter}",
            event_type="unknown",
            payload={"text": text},
            confidence=0.3,
        )

    # correction
    if any(w in text for w in ["不是", "改目标", "纠正", "不对", "错了"]):
        return AssistantEvent(
            event_id=f"ev_correction_{event_counter}",
            event_type="user_input",
            router_labels=["correction"],
            payload={"text": text},
            confidence=0.85,
        )

    # source input
    if any(w in text_lower for w in ["上传", "论文", "pdf", "代码仓库", "repo"]):
        return AssistantEvent(
            event_id=f"ev_source_{event_counter}",
            event_type="source_input",
            router_labels=["material_upload"],
            payload={"text": text},
            confidence=0.80,
        )

    # progress query
    if any(w in text for w in ["现在", "进度", "到哪", "状态", "怎么样", "如何"]):
        return AssistantEvent(
            event_id=f"ev_progress_{event_counter}",
            event_type="progress_query",
            router_labels=["status_inquiry"],
            payload={"text": text},
            confidence=0.80,
        )

    # goal update (generic)
    return AssistantEvent(
        event_id=f"ev_goal_{event_counter}",
        event_type="user_input",
        router_labels=["goal_update"],
        payload={"text": text},
        confidence=0.75,
    )


class AssistantRuntimeResult(BaseModel):
    session: AutoADAssistantSession
    prompt_id: str
    reply: str
    event: AssistantEvent
    violations: list[str] = Field(default_factory=list)


class DeterministicAssistantRuntime:
    """Fake-backend runtime for testing probe-first flow. No LLM."""

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self.runs_root = Path(runs_root)
        self._store = SessionStore(runs_root)
        self._registry = get_default_prompt_registry()
        self._artifacts = AssistantTaskArtifactService(runs_root, store=self._store)

    def handle_user_message(self, run_id: str, user_text: str) -> AssistantRuntimeResult:
        validate_run_id(self.runs_root, run_id)

        session = self._store.load_session(run_id)
        if session is None:
            session = AutoADAssistantSession(session_id=f"s_{run_id}", run_id=run_id)

        # Probe
        www = silent_probe(run_id, runs_root=self.runs_root)
        self._artifacts.write_what_we_know(www)

        # Route event
        event = route_user_text(user_text)
        self._store.append_event(run_id, event)

        # Apply transition
        old_mode = session.mode
        session = apply(session, event)
        self._store.append_transition(
            AssistantTransitionRecord(
                run_id=run_id,
                event_id=event.event_id,
                from_mode=old_mode,
                to_mode=session.mode,
                triggered_by=f"user_input:{event.event_id}",
            )
        )

        # Invariants
        violations = validate(session)

        # Prompt
        prompt_id = select_prompt_id(session.mode)

        # Fake reply
        reply = _fake_reply(session, www, event)

        self._store.save_session(session)
        return AssistantRuntimeResult(
            session=session,
            prompt_id=prompt_id,
            reply=reply,
            event=event,
            violations=violations,
        )


def _fake_reply(session, www, event) -> str:
    mode = session.mode
    baseline = www.baseline_method or "?"
    missing = ", ".join(www.missing_fields) if www.missing_fields else "无"

    if event.event_type == "unknown":
        return "抱歉，还不能稳定判断你的意图。能否再详细描述一下你的研究目标？"

    if "correction" in event.router_labels:
        text = event.payload.get("text", "")
        return (
            "我已按你的纠正更新理解。\n\n"
            f"你提到'{text}'，当前任务草案将聚焦于明确指标和约束，不决定具体方法或 patch。"
        )

    if mode == "goal_alignment":
        if www.has_baseline_contract:
            return (
                "不让你填长表单。\n\n"
                f"根据已有材料，我了解 baseline 是 {baseline}。"
                f"当前缺失 {missing}。\n"
                "请先提供论文/方法描述或目标代码仓库二选一，"
                "或直接一句话说明研究目标。"
            )
        return (
            "不让你填长表单。\n"
            "请先提供论文/方法描述或目标代码仓库二选一。"
        )

    if mode == "intent_structuring":
        variants = www.available_variants or []
        lines = [
            f"根据已有材料，我理解你想在 {baseline} 上继续异常检测方向。",
            f"当前已有 {len(variants)} 个候选 variant。",
        ]
        if www.missing_fields:
            lines.append(f"仍缺 {missing}，需要你这方面信息。")
        lines.append("我不会替你决定具体算法、超参数或 patch 位置。")
        return "\n".join(lines)

    return "收到。我将基于已有材料继续整理任务草案。"


# ─────────────────────────────────────────────────────────
# Live Alpha CLI (Round 7 — manual conversation with LLM)
# ─────────────────────────────────────────────────────────


def _call_llm(
    api_key: str,
    provider_url: str,
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-chat",
) -> str:
    base = provider_url.rstrip("/")
    if base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    resp = httpx.post(
        url,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )
    if resp.status_code != 200:
        return f"[LLM error: HTTP {resp.status_code}]"
    body = resp.json()
    return body["choices"][0]["message"]["content"]


def run_alpha(
    run_id: str,
    *,
    runs_root: str | Path = "runs",
    api_key: str = "",
    provider_url: str = "https://api.deepseek.com",
) -> None:
    """Manual Alpha conversation loop."""
    validate_run_id(runs_root, run_id)
    store = SessionStore(runs_root)
    registry = get_default_prompt_registry()
    artifacts = AssistantTaskArtifactService(runs_root, store=store)

    # ── Load or create session ──
    session = store.load_session(run_id)
    if session is None:
        session = AutoADAssistantSession(
            session_id=f"s_{run_id}",
            run_id=run_id,
        )
        store.save_session(session)
        print(f"[session] created: {session.session_id} (mode={session.mode})")
    else:
        print(f"[session] loaded: {session.session_id} (mode={session.mode})")

    # ── Silent probe ──
    print("\n[probe] scanning artifacts...")
    www = silent_probe(run_id, runs_root=runs_root)
    artifacts.write_what_we_know(www)
    print(f"  baseline={www.baseline_method or '?'}")
    print(f"  dataset={www.dataset or '?'}")
    print(f"  paper_methods={len(www.paper_methods)}")
    print(f"  variants={len(www.available_variants)}")
    print(f"  missing_fields={www.missing_fields}")

    # ── Interaction loop ──
    print("\n[alpha] start conversation (type /quit to exit, /draft to generate, /status to see session)")
    print(f"[alpha] current mode: {session.mode}")

    event_counter = 0
    while True:
        try:
            user_input = input("\n[YOU] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[alpha] exiting")
            break

        if not user_input:
            continue
        if user_input == "/quit":
            break
        if user_input == "/draft":
            _generate_draft(session, www, artifacts)
            continue
        if user_input == "/status":
            _show_status(session)
            continue

        # ── Create event ──
        event_counter += 1
        event = AssistantEvent(
            event_id=f"ev_{run_id}_{event_counter}",
            event_type="user_input",
            payload={"text": user_input},
        )
        store.append_event(run_id, event)

        # ── Apply transition ──
        old_mode = session.mode
        session = apply(session, event)
        new_mode = session.mode
        store.append_transition(
            run_id,
            event_id=event.event_id,
            from_mode=old_mode,
            to_mode=new_mode,
            triggered_by=f"user_input:{event.event_id}",
        )
        print(f"[mode] {old_mode} → {new_mode}")

        # ── Check invariants ──
        violations = validate(session)
        if violations:
            for v in violations:
                print(f"[invariant] WARNING: {v}")

        # ── Call LLM ──
        prompt_id = select_prompt_id(session.mode)
        system_prompt = registry.build_system_prompt(prompt_id)
        probe_text = _format_what_we_know(www)
        full_system = f"{system_prompt}\n\n## 当前已知信息\n{probe_text}"

        if not api_key:
            print("[LLM] no API key provided — displaying system prompt instead")
            print(f"[LLM] prompt_id={prompt_id}")
            print(f"[LLM] --- system prompt ---\n{full_system[:500]}...")
            reply = "[NO LLM — API key not set]"
        else:
            print("[LLM] thinking...")
            reply = _call_llm(api_key, provider_url, full_system, user_input)
            print(f"\n[ASSISTANT]\n{reply}")

        # ── Save ──
        store.save_session(session)
        print(f"[recorded] session saved (mode={session.mode})")


def _format_what_we_know(www) -> str:
    lines = []
    if www.baseline_method:
        lines.append(f"- baseline: {www.baseline_method} (commit {www.baseline_commit})")
    if www.dataset:
        lines.append(f"- dataset: {www.dataset}")
    if www.modifiable_hooks:
        lines.append(f"- modifiable hooks: {', '.join(www.modifiable_hooks[:5])}")
    if www.paper_methods:
        lines.append(f"- paper methods: {', '.join(www.paper_methods[:5])}")
    if www.available_variants:
        lines.append(f"- available variants: {', '.join(www.available_variants[:5])}")
    if www.missing_fields:
        lines.append(f"- MISSING: {', '.join(www.missing_fields)}")
    return "\n".join(lines) if lines else "(no known information)"


def _generate_draft(session, www, artifacts):
    print("[draft] generating ResearchTaskDraftV1 from current state...")
    # Minimal draft from WhatWeKnow
    draft = ResearchTaskDraftV1(
        run_id=session.run_id,
        draft_id=f"draft_{session.run_id}_1",
        metric_command=www.primary_metric or "UNKNOWN — please specify",
        metric_name=www.primary_metric or "UNKNOWN",
        metric_direction="maximize",
        baseline=www.baseline_method or "UNKNOWN — please specify",
        baseline_value=None,
        ambition="beat_baseline",
        scope="mixed",
        dataset=www.dataset,
        constraints=[],
    )
    result = artifacts.create_research_task_draft(session, draft)
    print(f"[draft] saved: {result['draft_path']}")
    print(f"[draft] has_blocking_gaps={result['has_blocking_gaps']}")


def _show_status(session):
    print(f"  mode: {session.mode}")
    print(f"  task.draft_ref: {session.task.draft_ref}")
    print(f"  task.confirmed_ref: {session.task.confirmed_ref}")
    print(f"  task.has_blocking_gaps: {session.task.has_blocking_gaps}")
    print(f"  task.ready_for_pipeline: {session.task.ready_for_pipeline}")
    print(f"  task.execution_approved: {session.task.execution_approved}")
