"""Deterministic response guard for Research Chat Alpha."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

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
    response_context: dict[str, Any] | None = None,
) -> GuardedReply:
    """Reject or rewrite replies that overclaim beyond available evidence."""
    violations: list[str] = []

    if _mentions_paper_content(reply) and not evidence_context.has_parsed_paper_evidence:
        violations.append("paper_content_without_parsed_artifact")

    if _mentions_repo_content(reply) and not evidence_context.has_repo_evidence:
        violations.append("repo_content_without_repo_evidence")

    if _promises_execution(reply) and not execution_approved:
        violations.append("execution_promise_without_approval")

    if _promises_background_material_acquisition(reply):
        violations.append("background_material_acquisition_promise")

    if _contains_prompt_injection_obedience(reply):
        violations.append("prompt_injection_instruction_obedience")

    unknown_source_ids = _unknown_source_ids(reply, response_context)
    if unknown_source_ids:
        violations.append("unknown_source_reference")

    unknown_parse_attempt_ids = _unknown_parse_attempt_ids(reply, response_context)
    if unknown_parse_attempt_ids:
        violations.append("unknown_parse_attempt_reference")

    if _treats_failed_or_partial_attempt_as_complete(reply, response_context):
        violations.append("failed_or_partial_attempt_as_complete_evidence")

    if _misidentifies_parsed_paper(reply, response_context):
        violations.append("parsed_paper_identity_conflict")

    if _unsupported_external_sota_claim(reply, user_input, response_context):
        violations.append("unsupported_external_sota_claim")

    if _violates_baseline_framework_constraint(reply, user_input):
        violations.append("baseline_framework_constraint_violation")

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
        r"我(将|会|现在|马上).*(apply patch|patch apply|打补丁|应用补丁)",
        r"我(将|会|现在|马上).*(runner|benchmark|基准测试)",
        r"确认后.*(开始执行|运行实验|修改代码)",
        r"确认后.*(patch|runner|benchmark|打补丁|基准测试)",
        r"直接.*(跑实验|运行实验|改代码|patch|runner|benchmark|打补丁|基准测试)",
    ]
    return _matches_any(patterns, text)


def _promises_background_material_acquisition(text: str) -> bool:
    patterns = [
        r"我(现在|马上|会|来|将).*?(搜索|搜集|查找|查一下|web_search|web_fetch|git_clone)",
        r"(正在|开始).*?(搜索|搜集|查找|web_search|web_fetch|git_clone)",
        r"(搜索|搜集|查找).*?(完成后|结束后).*?(回复|告诉|总结)",
        r"(预计|大概|需要).{0,12}\d+\s*[-到~]?\s*\d*\s*(分钟|min).*?(完成|回复|总结)",
        r"(完成后|稍后|等我).*?(主动)?(回复|告诉|总结)",
    ]
    material_tokens = ("搜索", "搜集", "查找", "资料", "论文", "方法", "SOTA", "web_search", "web_fetch", "git_clone")
    if not any(token.lower() in text.lower() for token in material_tokens):
        return False
    return _matches_any(patterns, text)


def _asks_for_artifacts(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("artifact", "artifacts", "制品", "论文 artifacts", "基于论文"))


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _contains_prompt_injection_obedience(text: str) -> bool:
    patterns = [
        r"(资料|source|来源|文件).*(要求|指示).*(忽略|覆盖|改变).*(系统|开发者|工具|权限|指令)",
        r"(我会|我将).*(忽略|覆盖|改变).*(系统|开发者|工具|权限|指令)",
        r"ignore (previous|system|developer) instructions",
        r"(override|bypass).*(tool|permission|system|developer)",
        r"解除.*(工具|权限|系统指令|开发者指令).*限制",
    ]
    return _matches_any(patterns, text)


def _unknown_source_ids(text: str, response_context: dict[str, Any] | None) -> list[str]:
    if response_context is None:
        return []
    known = _known_source_ids(response_context)
    if not known:
        return []
    return [source_id for source_id in _referenced_source_ids(text) if source_id not in known]


def _unknown_parse_attempt_ids(text: str, response_context: dict[str, Any] | None) -> list[str]:
    if response_context is None:
        return []
    known = _known_parse_attempts(response_context)
    if not known:
        return []
    return [attempt_id for attempt_id in _referenced_parse_attempt_ids(text) if attempt_id not in known]


def _treats_failed_or_partial_attempt_as_complete(
    text: str,
    response_context: dict[str, Any] | None,
) -> bool:
    if response_context is None:
        return False
    risky_attempts = {
        attempt_id
        for attempt_id, status in _known_parse_attempts(response_context).items()
        if status in {"failed", "partial"}
    }
    if not risky_attempts:
        return False
    mentioned = set(_referenced_parse_attempt_ids(text))
    if not mentioned & risky_attempts:
        return False
    return _matches_any(
        [
            r"(完整|完全|充分|可靠).*(证据|解析|正文|支持)",
            r"(可以|能够).*(完整|完全).*(基于|使用)",
            r"complete (evidence|parse|support)",
            r"fully supported",
        ],
        text,
    )


def _misidentifies_parsed_paper(text: str, response_context: dict[str, Any] | None) -> bool:
    paper_text = _paper_context_text(response_context).lower()
    if "simplenet" not in paper_text:
        return False
    return bool(re.search(r"(这篇论文|2303\.15140v2).{0,20}(是|就是).{0,20}patchcore", text, re.IGNORECASE))


def _unsupported_external_sota_claim(
    text: str,
    user_input: str,
    response_context: dict[str, Any] | None,
) -> bool:
    if not _asks_transfer_or_improvement(user_input):
        return False
    if _has_discovery_artifacts(response_context):
        return False
    risky = (
        "SOTA",
        "最新",
        "趋势",
        "最有效",
        "目前最",
        "DINOv2",
        "提升 2-5%",
        "提升 5",
        "AUC 提升",
    )
    return any(token.lower() in text.lower() for token in risky)


def _violates_baseline_framework_constraint(text: str, user_input: str) -> bool:
    user_lower = user_input.lower()
    if not (
        "patchcore" in user_lower
        or "pathcore" in user_lower
        or "baseline" in user_lower
        or "基础框架" in user_input
        or "基础架构" in user_input
    ):
        return False
    if not any(token in user_input for token in ("不能", "不可能", "不要", "不改", "不改变")):
        return False
    patterns = [
        r"(替换|换成|改成).{0,20}(backbone|ResNet|DINOv2|ViT)",
        r"DINOv2.{0,20}(替换|换掉|取代)",
        r"(最推荐|优先).{0,20}DINOv2",
    ]
    return _matches_any(patterns, text)


def _asks_transfer_or_improvement(text: str) -> bool:
    lowered = text.lower()
    return (
        any(token in text for token in ("迁移", "用到", "提升", "改进", "baseline", "基础框架", "最有可能"))
        or any(token in lowered for token in ("transfer", "improve", "baseline", "patchcore", "pathcore"))
    )


def _has_discovery_artifacts(response_context: dict[str, Any] | None) -> bool:
    if not isinstance(response_context, dict):
        return False
    facts = response_context.get("facts")
    if not isinstance(facts, dict):
        return False
    artifacts = facts.get("available_artifacts")
    if not isinstance(artifacts, list):
        return False
    return any(
        isinstance(item, str)
        and (
            "web_search_results" in item
            or "repository_discovery" in item
            or "discovery" in item
            or "acquisition" in item
        )
        for item in artifacts
    )


def _paper_context_text(response_context: dict[str, Any] | None) -> str:
    if not isinstance(response_context, dict):
        return ""
    facts = response_context.get("facts")
    if not isinstance(facts, dict):
        return ""
    paper_context = facts.get("paper_context")
    if not isinstance(paper_context, dict):
        return ""
    return str(paper_context)


def _known_source_ids(response_context: dict[str, Any]) -> set[str]:
    known: set[str] = set()
    facts = response_context.get("facts")
    if isinstance(facts, dict):
        source_id = facts.get("source_id")
        if isinstance(source_id, str):
            known.add(source_id)
        sources = facts.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict) and isinstance(source.get("source_id"), str):
                    known.add(source["source_id"])
    return known


def _known_parse_attempts(response_context: dict[str, Any]) -> dict[str, str]:
    attempts_by_id: dict[str, str] = {}
    facts = response_context.get("facts")
    if not isinstance(facts, dict):
        return attempts_by_id
    sources = facts.get("sources")
    if not isinstance(sources, list):
        return attempts_by_id
    for source in sources:
        if not isinstance(source, dict):
            continue
        active_id = source.get("active_parse_attempt_id")
        if isinstance(active_id, str):
            attempts_by_id.setdefault(active_id, "ok")
        attempts = source.get("parse_attempts")
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            attempt_id = attempt.get("parse_attempt_id")
            status = attempt.get("status")
            if isinstance(attempt_id, str):
                attempts_by_id[attempt_id] = status if isinstance(status, str) else "unknown"
    return attempts_by_id


def _referenced_source_ids(text: str) -> list[str]:
    return _dedupe(re.findall(r"\bsrc_[A-Za-z0-9_-]+\b", text))


def _referenced_parse_attempt_ids(text: str) -> list[str]:
    return _dedupe(re.findall(r"\bpa_[0-9]{6}\b|\blegacy_active\b", text))


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _safe_fallback_reply(violations: list[str], evidence_context: ResearchChatEvidenceContext) -> str:
    if "parsed_paper_identity_conflict" in violations:
        return (
            "这里需要更正：当前 parsed paper context 显示这篇论文不是 PatchCore 本身，"
            "不能把 2303.15140v2 说成 PatchCore。请以 paper_context 中的论文标题和组件为准，再讨论可迁移点。"
        )

    if "baseline_framework_constraint_violation" in violations:
        return (
            "你的 baseline 约束是保留 PatchCore 基础框架，因此不能优先建议替换 backbone 或切到外部框架。"
            "应优先考虑放在 PatchCore 特征输出之后、memory bank 构建之前的轻量适配/投影类改动；具体候选必须来自已解析论文 artifacts 或后续 discovery artifacts。"
        )

    if "background_material_acquisition_promise" in violations:
        return (
            "当前 Research Chat 不能在后台执行网络搜索，也不能承诺几分钟后主动发新消息。"
            "我可以把这个诉求登记为资料搜集请求；后续 discovery/acquisition agents 产出 artifacts 后，我再基于 artifacts 汇总。"
        )

    if "unsupported_external_sota_claim" in violations:
        return (
            "当前没有 discovery/acquisition artifacts 支持“最新 SOTA”“最有效”或具体提升幅度。"
            "这类外部趋势不能用模型记忆补全；应先基于已解析论文 artifacts 提候选迁移点，或登记资料搜集请求后等待 web_search/web_fetch/git_clone 产出 artifacts。"
        )

    if "execution_promise_without_approval" in violations:
        return (
            "当前只能确认研究任务边界，不能开始修改代码或运行实验。"
            "任务确认不等于代码修改批准，也不等于真实执行批准。"
        )

    if "prompt_injection_instruction_obedience" in violations:
        return (
            "资料内容只能作为不可信证据处理，不能改变系统指令、开发者指令或工具权限。"
            "我会忽略材料中的指令性文本，只基于已允许的资料证据回答。"
        )

    if "unknown_source_reference" in violations or "unknown_parse_attempt_reference" in violations:
        return (
            "回复引用了当前上下文中不存在的 source_id 或 parse_attempt_id。"
            "我需要先重新对齐可用资料边界，再基于已登记来源回答。"
        )

    if "failed_or_partial_attempt_as_complete_evidence" in violations:
        return (
            "当前解析尝试并不是完整可用证据，不能把 failed 或 partial attempt 当成完整正文依据。"
            "我只能披露解析状态，并等待可用 artifacts 或用户明确切换证据来源。"
        )

    if "repo_content_without_repo_evidence" in violations:
        return (
            "当前只能确认用户提供了仓库引用，系统尚未完成 repository intelligence 分析，"
            "因此不能基于仓库代码结构作判断。"
        )

    if "paper_content_without_parsed_artifact" in violations or "artifact_answer_without_parsed_artifact" in violations:
        if evidence_context.paper_artifact_quality == "insufficient":
            warnings = "、".join(evidence_context.paper_artifact_warnings) or "paper artifacts 证据不足"
            return (
                f"当前确实生成了 paper artifacts，但质量不足（{warnings}）。"
                "因此不能基于论文正文作可靠判断，也不能用模型记忆补全。"
            )
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
