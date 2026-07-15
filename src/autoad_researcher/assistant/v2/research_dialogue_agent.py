"""Single-call research dialogue and summary agent."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary


class ResearchDialogueResponse(BaseModel):
    """Schema-bound result of one research dialogue turn."""

    model_config = ConfigDict(extra="forbid")

    reply_to_user: str = Field(min_length=1)
    summary: ResearchIntentSummary
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
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> ResearchDialogueResponse:
        if not api_key:
            return ResearchDialogueResponse(
                reply_to_user="当前没有可用的对话模型连接，材料任务仍可在后台处理。",
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
            model="deepseek-v4-flash",
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
        system = f"""你是 AutoAD Research Assistant。你帮助研究者对齐材料和研究目标。

当前可用材料：{evidence_summary}
上一轮研究摘要：{previous}
最近对话：{recent_dialogue}

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
- reply_to_user 保持两到四个短自然段，只完成目标对齐、关键冲突和下一步，不展开完整实施教程；不使用编号、项目符号、字段清单或内部状态术语，除非用户明确要求列表
- 当用户约束已足以形成高层研究或评估计划时，缺少候选方法源码只影响源码级映射，不阻塞高层方案；先给出有证据边界的方案，把材料需求作为普通说明，blocking_question 保持 null
- 缺少具体材料时不要断言某一版本的输入尺寸、张量形状或实现细节；只能说明可由通用架构推导的风险以及仍需材料验证的部分
- 对“任务名称相似所以可直接集成”“即插即用”等未经材料支持的跨领域前提，必须明确拒绝确定实现，只给兼容性验证路线；在材料证明输入结构、训练目标和评分语义兼容前，禁止给出并行分支、特征融合、序列化、损失组合或打分组合等桥接细节，即使附带 caveat 也不行
- 对性能或算子优化，参考实现正确性和同条件 benchmark 是验收前提；指定目标文件尚未形成 evidence 时不得猜测算子内容
- 当模型参数、优化器状态、激活或运行时需求明显超过用户硬件时，即使 offload、checkpointing 等手段可能缓解，也必须把训练/运行可行性写入 unresolved_conflicts；在真实配置和资源未验证前不得宣称已经可行
- 当前只做研究对齐与计划；不得声称已经修改代码、创建实验 Session、运行训练或执行实验

只输出 JSON object，不要输出 Markdown code fence。输出结构：
{{"reply_to_user":"...","summary":{{"goal":"...","confirmed_facts":["..."],"inferred_facts":[{{"statement":"...","basis":"..."}}],"unresolved_conflicts":[{{"statement":"...","basis":"..."}}],"blocking_question":null}}}}"""
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
