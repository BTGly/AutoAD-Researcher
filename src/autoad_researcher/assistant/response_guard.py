"""Deterministic response guard for Research Chat Alpha."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from autoad_researcher.assistant.research_context_builder import ResearchChatEvidenceContext


@dataclass(frozen=True)
class GuardedReply:
    reply: str
    violations: list[str] = field(default_factory=list)


def guard_research_chat_reply(
    *,
    reply: str,
    user_input: str,
    evidence_context: ResearchChatEvidenceContext,
    execution_approved: bool = False,
) -> GuardedReply:
    """Reject or rewrite replies that overclaim beyond available evidence."""
    violations: list[str] = []

    if _mentions_paper_content(reply) and not evidence_context.has_parsed_paper_evidence:
        violations.append("paper_content_without_parsed_artifact")

    if _mentions_repo_content(reply) and not evidence_context.has_repo_evidence:
        violations.append("repo_content_without_repo_evidence")

    if _promises_execution(reply) and not execution_approved:
        violations.append("execution_promise_without_approval")

    if _asks_for_artifacts(user_input) and not evidence_context.has_parsed_paper_evidence:
        violations.append("artifact_answer_without_parsed_artifact")

    if not violations:
        return GuardedReply(reply=reply, violations=[])

    return GuardedReply(
        reply=_safe_fallback_reply(violations, evidence_context),
        violations=violations,
    )


def _mentions_paper_content(text: str) -> bool:
    patterns = [
        r"我已经(读过|看过|阅读).*论文",
        r"已(读过|看过|阅读).*论文",
        r"论文(提出|采用|使用|证明|报告|显示|核心)",
        r"根据论文(正文|内容|方法)",
    ]
    return _matches_any(patterns, text)


def _mentions_repo_content(text: str) -> bool:
    patterns = [
        r"我已经(看过|分析).*仓库",
        r"仓库中(实现|包含|定义)",
        r"代码结构",
        r"repo 中(实现|包含|定义)",
    ]
    return _matches_any(patterns, text)


def _promises_execution(text: str) -> bool:
    patterns = [
        r"我(将|会|现在|马上).*修改代码",
        r"我(将|会|现在|马上).*运行实验",
        r"我(将|会|现在|马上).*开始执行",
        r"确认后.*(开始执行|运行实验|修改代码)",
        r"直接.*(跑实验|运行实验|改代码)",
    ]
    return _matches_any(patterns, text)


def _asks_for_artifacts(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("artifact", "artifacts", "制品", "论文 artifacts", "基于论文"))


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _safe_fallback_reply(violations: list[str], evidence_context: ResearchChatEvidenceContext) -> str:
    if "execution_promise_without_approval" in violations:
        return (
            "当前只能确认研究任务边界，不能开始修改代码或运行实验。"
            "任务确认不等于代码修改批准，也不等于真实执行批准。"
        )

    if "repo_content_without_repo_evidence" in violations:
        return (
            "当前只能确认用户提供了仓库引用，系统尚未完成 repository intelligence 分析，"
            "因此不能基于仓库代码结构作判断。"
        )

    if "paper_content_without_parsed_artifact" in violations or "artifact_answer_without_parsed_artifact" in violations:
        if evidence_context.uploaded_unparsed_sources:
            return (
                "我看到资料已进入当前任务，但目前还没有可用于回答正文问题的 parsed paper artifacts。"
                "在解析完成前，我只能确认文件或引用已提供，不能基于论文正文判断方法细节。"
            )
        if evidence_context.candidate_references:
            return (
                "当前只能确认你提供了引用标识，系统尚未解析对应 PDF 或生成 paper artifacts，"
                "因此不能基于论文正文作判断。"
            )
        return (
            "当前尚未看到 parsed paper artifacts，因此不能基于论文正文作判断。"
            "请先上传并解析论文材料。"
        )

    return "当前回复缺少足够证据支撑，我需要先基于已解析 artifacts 或用户确认信息重新整理。"
