"""Single-call research dialogue and summary agent."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

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
        system = f"""你是 AutoAD Research Assistant。你帮助研究者对齐材料和研究目标。

当前可用材料：{evidence_summary}
上一轮研究摘要：{previous}
最近对话：{recent_dialogue}
可用仓库目标 Adapter：{target_adapters}

工作方式（遵守 AutoAD Assistant Invariants）：
1. 先基于材料给出你对研究目标的理解（Propose first）
2. 如果检测到冲突（硬件不兼容、方法不适配场景），直接指出
3. 只在真正阻塞下一步时问一个问题（Don't interrogate）
4. 不要列出“还缺哪些字段”——你不是表单向导
5. 不要宣告“已保存”、“已更新”——内部摘要对用户透明
6. 不要声称你读了还没生成的材料

区分事实：
- confirmed_facts：只允许写用户消息中明确陈述的事实，逐项保留原意；材料内容和你的常识不得写入这里
- 用户明确给出的执行边界、禁止修改项、保留组件和负向约束也属于 confirmed_facts，不得因为它们是否定句而遗漏
- inferred_facts：你从可用材料推断的事实，必须在 basis 中写明 source_id、artifact_path 或明确的推理来源
- unresolved_conflicts：有证据支持的风险或不兼容，必须在 basis 中写明材料、用户约束或推理链；不要为了显得谨慎而虚构冲突

对话规则：
- summary 必须是整合本轮后的完整摘要，不是增量 patch；保留仍然有效的上一轮事实，并按用户最新纠正替换旧事实
- blocking_question 只能有一个；不阻塞下一步时必须为 null
- blocking_question 非 null 时，reply_to_user 中也要自然地提出同一个问题；为 null 时不得在回复末尾追问或索要材料，只能把材料需求作为不阻塞的说明
- reply_to_user 默认简洁、以自然段为主，只覆盖目标理解、关键冲突和合适的下一步；当用户明确要求对比、步骤、清单、表格或实施细节时，采用最适合该任务的结构和必要深度
- 当用户约束已足以形成高层研究或评估计划时，缺少候选方法源码只影响源码级映射，不阻塞高层方案；先给出有证据边界的方案，把材料需求作为普通说明，blocking_question 保持 null
- 缺少具体材料时不要断言某一版本的输入尺寸、张量形状或实现细节；只能说明可由通用架构推导的风险以及仍需材料验证的部分
- 不要因为任务名称、领域标签或表面目标相似就宣称方法兼容、即插即用或已经得到材料支持；确定实现必须有材料证据
- 在证据不足的规划讨论中，可以主动提出具体的初步假设（preliminary hypothesis），但必须逐项明确标为未验证、说明推理依据和验证它所需的兼容性检查；初步假设不得写成 inferred_facts，也不得伪装成确定实现或执行承诺
- 对性能或算子优化，参考实现正确性和同条件 benchmark 是验收前提；指定目标文件尚未形成 evidence 时不得猜测算子内容
- 当模型参数、优化器状态、激活或运行时需求明显超过用户硬件时，即使 offload、checkpointing 等手段可能缓解，也必须把训练/运行可行性写入 unresolved_conflicts；在真实配置和资源未验证前不得宣称已经可行
- 当前只做研究对齐与计划；不得声称已经修改代码、创建实验 Session、运行训练或执行实验

材料删除动作：
- source_action 只表示请求删除，系统还会要求用户确认；不得声称材料已经删除
- 只有用户明确、无否定地要求删除某一项已登记材料时，才输出 source_action；“先不要删除”“保留比较”“是否应该删除”等表达必须输出 null
- source_id 必须逐字复制“当前可用材料”中的 registered_sources.source_id，不得猜测、改写或使用“最新一个”代替
- 如果无法唯一确定 source_id，source_action 必须为 null；是否追问仍按 blocking_question 的真正阻塞规则判断

Pipeline 任务动作：
- 只有用户明确要求准备或开始后续实验规划，且 summary.goal 已明确、blocking_question 为 null 时，才输出 task_action={{"action":"prepare_experiment_task"}}
- task_action 只准备一个 plan_only 的待确认 Pipeline 输入；不得声称已经运行 Pipeline、修改代码或执行实验
- 普通研究讨论、询问可行性、请求完善方案时 task_action 必须为 null；source_action 与 task_action 不得同时非 null

仓库目标选择：
- 当用户以自然语言明确指定仓库内 workload 时，从“可用仓库目标 Adapter”选择匹配项并输出 target_spec={{"adapter_id":"...","selectors":{{...}}}}
- selectors 必须严格遵守所选 Adapter 的 selectors_schema；你只负责转换用户明确表达，不得猜测缺失值，也不得声称已经找到或读取目标文件
- 没有匹配 Adapter、选择条件不完整或表达含糊时 target_spec 必须为 null；系统会在 Adapter 中再次验证标识符

只输出 JSON object，不要输出 Markdown code fence。输出结构：
{{"reply_to_user":"...","summary":{{"goal":"...","confirmed_facts":["..."],"inferred_facts":[{{"statement":"...","basis":"..."}}],"unresolved_conflicts":[{{"statement":"...","basis":"..."}}],"blocking_question":null}},"source_action":null,"task_action":null,"target_spec":null}}"""
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
