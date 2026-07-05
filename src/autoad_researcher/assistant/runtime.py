"""Deterministic AutoAD Assistant runtime skeleton.

This module wires the v0.5 control path without using a real LLM and without
triggering pipeline execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.events import AssistantEvent, RouterLabel
from autoad_researcher.assistant.probe import WhatWeKnow, silent_probe
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.session import AutoADAssistantSession, AssistantMode
from autoad_researcher.assistant.session_store import AssistantTransitionRecord, SessionStore
from autoad_researcher.assistant.transition_policy import apply as apply_transition
from autoad_researcher.assistant.transition_policy import validate as validate_session


class AssistantRuntimeResult(BaseModel):
    """Machine-readable result for one deterministic assistant turn."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    session: AutoADAssistantSession
    event: AssistantEvent
    what_we_know: WhatWeKnow
    prompt_id: str
    reply: str
    violations: list[str] = Field(default_factory=list)


class DeterministicAssistantBackend(Protocol):
    """Backend protocol for deterministic tests and future LLM adapters."""

    def generate_reply(
        self,
        *,
        session: AutoADAssistantSession,
        event: AssistantEvent,
        what_we_know: WhatWeKnow,
        prompt_id: str,
    ) -> str:
        ...


@dataclass
class FakeIntentAlignmentBackend:
    """Deterministic propose-don't-interrogate backend for Round 4 tests."""

    def generate_reply(
        self,
        *,
        session: AutoADAssistantSession,
        event: AssistantEvent,
        what_we_know: WhatWeKnow,
        prompt_id: str,
    ) -> str:
        labels = set(event.router_labels)
        if event.event_type == "unknown":
            return "我还不能稳定判断你的意思。请用一句话说明你想调整研究目标、补充材料，还是确认任务草案。"
        if "correction" in labels:
            text = _event_text(event)
            return f"我已按你的纠正更新理解：{text or '以你的最新说明为准'}。我会回到任务意图整理，只更新目标边界，不决定具体方法或 patch。"
        if what_we_know.evidence_artifacts:
            known = _known_summary(what_we_know)
            gaps = _blocking_gaps(what_we_know)
            return (
                f"我先基于已有 artifact 给出任务理解：{known}。"
                f"当前只剩这些阻塞缺口需要确认：{gaps}。"
                "我会先整理五要素任务草案；这里不会决定算法、超参数、patch hook 或具体 variant。"
            )
        return (
            "我现在还没有可用的 run artifact。先不让你填长表单；"
            "最有价值的下一步是提供论文/方法描述或目标代码仓库二选一。"
            "如果两者都没有，也可以先用一句话描述你想优化的异常检测目标。"
        )


class DeterministicAssistantRuntime:
    """Round 4 runtime skeleton for local deterministic intent-alignment flows."""

    def __init__(
        self,
        *,
        runs_root: str = "runs",
        store: SessionStore | None = None,
        selector: PromptSelector | None = None,
        backend: DeterministicAssistantBackend | None = None,
    ) -> None:
        self._runs_root = runs_root
        self._store = store or SessionStore(runs_root=runs_root)
        self._selector = selector or PromptSelector()
        self._backend = backend or FakeIntentAlignmentBackend()

    def handle_user_message(self, run_id: str, text: str, *, session_id: str | None = None) -> AssistantRuntimeResult:
        session = self._store.load_session(run_id) or AutoADAssistantSession(
            session_id=session_id or f"assistant_{run_id}",
            run_id=run_id,
        )
        before_mode = session.mode
        what_we_know = silent_probe(run_id, runs_root=self._runs_root)
        event = route_user_text(text)
        session = apply_transition(session, event)
        session = self._apply_probe_first_mode(session, event, what_we_know)
        violations = validate_session(session)
        prompt_id = self._selector.prompt_id_for_mode(session.mode)
        reply = self._backend.generate_reply(
            session=session,
            event=event,
            what_we_know=what_we_know,
            prompt_id=prompt_id,
        )

        self._store.append_event(run_id, event)
        self._store.append_transition(
            AssistantTransitionRecord(
                run_id=run_id,
                event_id=event.event_id,
                from_mode=before_mode,
                to_mode=session.mode,
                reason="deterministic_runtime_round4",
                violations=violations,
            )
        )
        self._store.save_session(session)

        return AssistantRuntimeResult(
            run_id=run_id,
            session=session,
            event=event,
            what_we_know=what_we_know,
            prompt_id=prompt_id,
            reply=reply,
            violations=violations,
        )

    @staticmethod
    def _apply_probe_first_mode(
        session: AutoADAssistantSession,
        event: AssistantEvent,
        what_we_know: WhatWeKnow,
    ) -> AutoADAssistantSession:
        if event.event_type == "user_input" and "correction" in event.router_labels:
            return session.model_copy(update={"mode": "intent_structuring"})
        if event.event_type == "user_input" and what_we_know.evidence_artifacts and session.mode == "goal_alignment":
            return session.model_copy(update={"mode": "intent_structuring"})
        return session


def route_user_text(text: str) -> AssistantEvent:
    """Tiny deterministic router for Round 4; not a full intent router."""
    normalized = text.strip()
    labels: list[RouterLabel] = []
    event_type = "user_input"
    confidence = 0.8

    if not normalized:
        event_type = "unknown"
        confidence = 0.2
    elif any(token in normalized.lower() for token in ["status", "progress"]) or any(
        token in normalized for token in ["到哪", "进度", "状态"]
    ):
        event_type = "progress_query"
        labels.append("status_inquiry")
    elif any(token in normalized for token in ["不是", "纠正", "改成", "不对"]):
        labels.append("correction")
    elif any(token in normalized.lower() for token in ["yes", "confirm"]) or any(
        token in normalized for token in ["确认", "同意"]
    ):
        event_type = "task_decision"
        labels.append("confirmation")
    elif any(token in normalized.lower() for token in ["pdf", "repo"]) or any(
        token in normalized for token in ["上传", "材料", "论文", "仓库"]
    ):
        event_type = "source_input"
        labels.append("material_upload")
    else:
        labels.append("goal_update")

    return AssistantEvent(
        event_id=_event_id(normalized),
        event_type=event_type,  # type: ignore[arg-type]
        payload={"text": normalized},
        router_labels=labels,
        confidence=confidence,
    )


def _event_id(text: str) -> str:
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"ev_{digest}"


def _event_text(event: AssistantEvent) -> str:
    value = event.payload.get("text")
    return value if isinstance(value, str) else ""


def _known_summary(what_we_know: WhatWeKnow) -> str:
    parts: list[str] = []
    if what_we_know.baseline_method:
        parts.append(f"baseline 是 {what_we_know.baseline_method}")
    if what_we_know.baseline_commit:
        parts.append(f"仓库 commit 已记录")
    if what_we_know.modifiable_hooks:
        parts.append(f"可修改 hook 包括 {', '.join(what_we_know.modifiable_hooks[:3])}")
    if what_we_know.paper_methods:
        parts.append(f"论文/idea 指向 {what_we_know.paper_methods[0]}")
    if what_we_know.available_variants:
        parts.append(f"已有候选 variant {what_we_know.available_variants[0]}")
    return "；".join(parts) if parts else "已有 artifact 可用，但字段仍需整理"


def _blocking_gaps(what_we_know: WhatWeKnow) -> str:
    gaps = [field for field in what_we_know.missing_fields if field in {"dataset", "primary_metric", "category", "metric_direction"}]
    return "、".join(gaps[:4]) if gaps else "暂无明显阻塞缺口"
