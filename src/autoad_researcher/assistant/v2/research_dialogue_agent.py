"""Single-call research dialogue and summary agent."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary
from autoad_researcher.assistant.v2.task_bridge import TaskInstruction
from autoad_researcher.assistant.v2.target_adapter import get_target_adapter_registry


class SourceInstruction(BaseModel):
    """A destructive source action that still requires user confirmation."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["request_source_removal"]
    source_id: str = Field(min_length=1)
    label_hint: str = ""
    reason: str = ""


class TargetSpec(BaseModel):
    """Semantic benchmark selector validated by a deterministic adapter."""

    model_config = ConfigDict(extra="forbid")

    adapter_id: str = Field(min_length=1)
    selectors: dict[str, Any]


class ResearchDialogueResponse(BaseModel):
    """Schema-bound result of one research dialogue turn."""

    model_config = ConfigDict(extra="forbid")

    reply_to_user: str = Field(min_length=1)
    summary: ResearchIntentSummary
    source_action: SourceInstruction | None = None
    task_action: TaskInstruction | None = None
    target_spec: TargetSpec | None = None
    _should_persist: bool = PrivateAttr(default=False)

    @property
    def should_persist(self) -> bool:
        return self._should_persist

    def visible_reply(self) -> str:
        reply = self.reply_to_user.strip()
        question = (self.summary.blocking_question or "").strip()
        if question and question not in reply:
            return f"{reply}\n\n{question}"
        return reply


class ResearchDialogueAgent:
    """Produce the user reply and complete next summary in one LLM call."""

    @classmethod
    def respond(
        cls,
        *,
        user_input: str,
        evidence_state: dict[str, Any],
        last_summary: ResearchIntentSummary | None,
        transcript_tail: list[dict[str, Any]] | None = None,
        api_key: str = "",
        provider_url: str = "",
        model: str = "",
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> ResearchDialogueResponse:
        if not api_key:
            return ResearchDialogueResponse(
                reply_to_user="当前没有可用的对话模型连接，材料任务仍可在后台处理。",
                summary=last_summary or ResearchIntentSummary(),
            )
        if not model.strip():
            return ResearchDialogueResponse(
                reply_to_user="当前没有配置对话模型，材料任务仍可在后台处理。",
                summary=last_summary or ResearchIntentSummary(),
            )

        messages = cls.build_messages(
            user_input=user_input,
            evidence_state=evidence_state,
            last_summary=last_summary,
            transcript_tail=transcript_tail,
        )
        from autoad_researcher.ui.chat_client import call_research_chat

        result = call_research_chat(
            api_key,
            provider_url,
            messages,
            model=model,
            timeout_s=30,
        )
        payload = _parse_json_object(str(result.get("reply") or ""))
        if result.get("error") or payload is None:
            return ResearchDialogueResponse(
                reply_to_user="这轮回复生成失败了，请重试。",
                summary=last_summary or ResearchIntentSummary(),
            )
        try:
            response = ResearchDialogueResponse.model_validate(payload)
        except Exception:
            return ResearchDialogueResponse(
                reply_to_user="这轮回复格式无效，请重试。",
                summary=last_summary or ResearchIntentSummary(),
            )
        response._should_persist = True
        if on_reply_delta is not None:
            on_reply_delta(response.visible_reply())
        return response

    @classmethod
    def build_messages(
        cls,
        *,
        user_input: str,
        evidence_state: dict[str, Any],
        last_summary: ResearchIntentSummary | None,
        transcript_tail: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, str]]:
        evidence_summary = _json_text(_compact_evidence_state(evidence_state))
        previous = _json_text(
            (last_summary or ResearchIntentSummary()).model_dump(mode="json")
        )
        recent_dialogue = _json_text(_clean_transcript(transcript_tail))
        target_adapters = _json_text(get_target_adapter_registry().prompt_catalog())
        behavior_contract = PromptSelector().build_research_dialogue_prompt()
        runtime_context = f"""本轮上下文：
当前可用材料：{evidence_summary}
上一轮研究摘要：{previous}
最近对话：{recent_dialogue}
可用仓库目标 Adapter：{target_adapters}"""
        system = behavior_contract.rstrip() + "\n\n" + runtime_context
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_input},
        ]


def _clean_transcript(transcript_tail: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in (transcript_tail or [])[-8:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            cleaned.append({"role": role, "content": content})
    return cleaned


def _compact_evidence_state(state: dict[str, Any]) -> dict[str, Any]:
    usable: list[dict[str, Any]] = []
    for item in (state.get("usable_evidence") or [])[:12]:
        if not isinstance(item, dict):
            continue
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        usable.append({
            "source_id": item.get("source_id"),
            "evidence_type": item.get("evidence_type"),
            "artifact_path": item.get("artifact_path"),
            "parser_name": item.get("parser_name"),
            "summary": str(item.get("summary") or "")[:3000],
            "metadata": {
                key: raw.get(key)
                for key in (
                    "analysis_id",
                    "repository_commit",
                    "compatibility_status",
                    "quality_level",
                    "warnings",
                    "fatal_errors",
                )
                if raw.get(key) is not None
            },
        })
    return {
        "usable_evidence": usable,
        "unparsed_sources": state.get("unparsed_sources") or [],
        "unusable_parsed_sources": state.get("unusable_parsed_sources") or [],
        "pending_jobs": state.get("pending_jobs") or [],
        "failed_jobs": state.get("failed_jobs") or [],
        "answerability": state.get("answerability") or {},
        "current_turn_material_actions": state.get("current_turn_material_actions") or {},
        "registered_sources": state.get("registered_sources") or [],
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None
    return payload if isinstance(payload, dict) else None


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
