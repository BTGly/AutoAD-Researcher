"""AutoAD Assistant Runtime -- deterministic skeleton for testing (Round 4)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from autoad_researcher.assistant.events import AssistantEvent, AssistantEventType
from autoad_researcher.assistant.probe import WhatWeKnow, silent_probe
from autoad_researcher.assistant.prompt_registry import get_default_prompt_registry
from autoad_researcher.assistant.prompt_selector import select_prompt_id
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.assistant.session_store import (
    AssistantTransitionRecord,
    SessionStore,
)
from autoad_researcher.assistant.task_artifacts import AssistantTaskArtifactService
from autoad_researcher.assistant.transition_policy import apply, validate
from autoad_researcher.core.run_id import validate_run_id


# ─────────────────────────────────────────────────────────
# Deterministic Runtime (Round 4 — fake backend for tests)
# ─────────────────────────────────────────────────────────


def route_user_text(text: str) -> AssistantEvent:
    """Coarse envelope router -- NOT behavior enumeration."""
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
        labels = ["correction"]
        if any(w in text for w in ["迁移", "用到", "用在", "方法迁移"]):
            labels.append("method_transfer")
        return AssistantEvent(
            event_id=f"ev_correction_{event_counter}",
            event_type="user_input",
            router_labels=labels,
            payload={"text": text},
            confidence=0.85,
        )

    # direct execution request: must stay in intent alignment, no execution approval
    if any(w in text for w in ["直接改代码", "直接跑", "跑实验", "运行实验", "开始执行", "执行 pipeline"]):
        return AssistantEvent(
            event_id=f"ev_execution_{event_counter}",
            event_type="user_input",
            router_labels=["execution_request"],
            payload={"text": text},
            confidence=0.85,
        )

    # ambiguous reproduction/transfer intent
    if "复现" in text and any(w in text for w in ["用到", "用在", "迁移", "我的项目", "项目里"]):
        return AssistantEvent(
            event_id=f"ev_ambiguous_{event_counter}",
            event_type="user_input",
            router_labels=["ambiguous_reproduction_or_transfer"],
            payload={"text": text},
            confidence=0.85,
        )

    if "复现" in text and "baseline" in text_lower:
        return AssistantEvent(
            event_id=f"ev_baseline_repro_{event_counter}",
            event_type="user_input",
            router_labels=["baseline_reproduction"],
            payload={"text": text},
            confidence=0.80,
        )

    if "复现" in text and "论文" in text:
        return AssistantEvent(
            event_id=f"ev_paper_repro_{event_counter}",
            event_type="user_input",
            router_labels=["paper_reproduction"],
            payload={"text": text},
            confidence=0.75,
        )

    if any(w in text for w in ["迁移", "用到 PatchCore", "用在 PatchCore", "方法迁移"]):
        return AssistantEvent(
            event_id=f"ev_transfer_{event_counter}",
            event_type="user_input",
            router_labels=["method_transfer"],
            payload={"text": text},
            confidence=0.80,
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
        session = _apply_probe_first_mode(session, www, event)
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


def _apply_probe_first_mode(
    session: AutoADAssistantSession,
    what_we_know: WhatWeKnow,
    event: AssistantEvent,
) -> AutoADAssistantSession:
    if session.mode != "goal_alignment":
        return session
    if event.event_type in {"unknown", "progress_query", "source_input"}:
        return session
    if not _has_probe_material(what_we_know):
        return session
    return session.model_copy(update={"mode": "intent_structuring"})


def _has_probe_material(what_we_know: WhatWeKnow) -> bool:
    return any(
        [
            what_we_know.has_baseline_contract,
            what_we_know.has_paper_artifacts,
            what_we_know.has_context_draft,
            what_we_know.has_implementation_variants,
            what_we_know.has_transfer_analysis,
        ]
    )


def _fake_reply(session, www, event) -> str:
    mode = session.mode
    baseline = www.baseline_method or "?"
    missing = ", ".join(www.missing_fields) if www.missing_fields else "无"

    if event.event_type == "unknown":
        return "抱歉，还不能稳定判断你的意图。能否再详细描述一下你的研究目标？"

    if "correction" in event.router_labels:
        text = event.payload.get("text", "")
        if "method_transfer" in event.router_labels:
            return (
                "我已按你的纠正更新理解。\n\n"
                f"你提到'{text}'，当前任务应理解为方法迁移 / baseline 优化，"
                "不是完整复现论文。当前不决定具体 hook、patch、discriminator 结构、超参数或实验执行。"
            )
        return (
            "我已按你的纠正更新理解。\n\n"
            f"你提到'{text}'，当前任务草案将聚焦于明确指标和约束，不决定具体方法或 patch。"
        )

    if "execution_request" in event.router_labels:
        return (
            "当前还不能直接改代码或运行实验。\n"
            "我只能先帮你确认研究任务边界；任务确认不等于 patch approval，也不等于 execution approval。"
        )

    if "ambiguous_reproduction_or_transfer" in event.router_labels:
        return (
            "当前理解有两种可能：\n"
            "1. 完整复现论文结果；\n"
            "2. 先验证论文中的思想是否能迁移到你的异常检测项目。\n\n"
            "你说“看看能不能用到我的项目里”，所以我不能直接把它定为完整复现。"
            "请确认你更偏向完整复现，还是迁移其中思想到已有 baseline。"
        )

    if "method_transfer" in event.router_labels:
        return (
            "当前理解：这是一个方法迁移 / baseline 优化任务，不是完整复现论文。\n"
            "我会先确认 metric、baseline、dataset/category、ambition、constraints，"
            "当前不决定 hook、patch、超参数或实验执行。"
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
            f"根据已有材料，我理解 baseline 是 {baseline}，你想继续异常检测方向。",
            f"当前已有候选 variant {len(variants)} 个。",
        ]
        if www.missing_fields:
            lines.append(f"仍缺 {missing}，需要你这方面信息。")
        lines.append("我不会替你决定具体算法、超参数或 patch 位置。")
        return "\n".join(lines)

    return "收到。我将基于已有材料继续整理任务草案。"
