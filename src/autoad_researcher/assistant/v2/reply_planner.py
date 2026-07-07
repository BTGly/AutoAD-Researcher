"""Reply planner for V2. answerability-driven, not ResponseMode-driven."""

from __future__ import annotations

from typing import Any


def plan_reply(
    llm_context: dict[str, Any],
    user_input: str,
) -> tuple[str, str]:
    """Return (reply_kind, reply_text)."""

    answerability = llm_context.get("answerability", {})
    can_answer = answerability.get("can_answer", False)
    blocking = answerability.get("blocking_next_step", "")
    basis = answerability.get("basis", [])
    usable = llm_context.get("usable_evidence", [])
    candidates = llm_context.get("candidate_sources", [])
    unparsed = llm_context.get("unparsed_sources", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    readable = llm_context.get("readable_summaries", [])

    if can_answer and ("迁移" in user_input or "transfer" in user_input.lower() or "能迁移" in user_input):
        return _reply_transfer_question(llm_context, user_input)

    if can_answer and readable:
        summary_text = "\n\n".join(readable[:3])
        return "answer_from_evidence", (
            f"基于已解析资料：\n\n{summary_text}\n\n"
            "你可以继续问具体方法、可迁移性、或研究方案。"
        )

    if blocking == "parse" and unparsed:
        return "need_parse", (
            f"当前有 {len(unparsed)} 个 source 已登记但尚未解析。\n"
            "上传 PDF 后会自动触发后台解析，完成后弹出通知。"
        )

    if blocking == "fetch" and candidates:
        return "candidate_only", (
            f"当前有 {len(candidates)} 个候选来源（candidate_source_only）。\n"
            "这些来源尚未 fetch/parse，不能作为 supported facts。\n"
            "你可以上传 PDF 或粘贴 arXiv 链接触发下载和解析。"
        )

    if blocking == "intake":
        return "need_acquire", (
            "当前没有已登记的资料。\n\n"
            "你可以：\n"
            "- 上传 PDF 论文（点击右下角 +）\n"
            "- 粘贴 arXiv / GitHub 链接到对话框\n"
            "- 搜索最新论文\n"
            "- 描述你的研究方向"
        )

    if confirmed:
        parts = [f"当前已确认：{confirmed.get('dataset', '未指定')} / {confirmed.get('baseline', '未指定')}"]
        if confirmed.get("research_direction"):
            parts.append(f"研究方向：{confirmed['research_direction']}")
        return "answer", "\n".join(parts)

    return "answer", "收到。上传 PDF 或粘贴链接开始分析。"


def _reply_transfer_question(llm_context: dict[str, Any], user_input: str) -> tuple[str, str]:
    usable = llm_context.get("usable_evidence", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    baseline = confirmed.get("baseline", "unknown baseline")

    paper_context = ""
    for e in usable:
        raw = e.get("raw", {})
        title = raw.get("title", "")
        method = raw.get("proposed_method", "")
        if title:
            paper_context += f"**{title}**\n"
        if method:
            paper_context += f"Method: {method}\n"

    if paper_context:
        return "answer_from_evidence", (
            f"基于已解析论文：\n\n{paper_context}\n\n"
            f"在不改变 {baseline} 主框架的前提下，"
            "最可能迁移的模块需要对比论文的具体方法和当前框架的接口。"
            "上传论文 PDF 或提供更多信息后，我可以给出具体建议。"
        )

    return "need_parse", "当前没有已解析的论文内容。上传 PDF 后自动解析，完成后可以回答迁移问题。"
