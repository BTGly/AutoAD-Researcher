"""Reply planner for V2 — LLM-first, answerability-driven fallback."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from autoad_researcher.assistant.prompt_selector import PromptSelector


_CONTRACT_REPLY_KEYS = {
    "reply_to_user",
    "contract_updates",
    "missing_required_fields",
    "new_user_confirmed_fields",
    "next_question",
    "optional_hints_detected",
    "ready_for_confirmation",
    "ready_for_experiment_agents",
    "ready_for_plan",
}


def plan_reply(
    llm_context: dict[str, Any],
    user_input: str,
    *,
    api_key: str = "",
    provider_url: str = "",
    on_delta: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    """Return (reply_kind, reply_text).

    All user input goes through LLM when api_key is available.
    Fallback is evidence-state structured — no keyword matching, no fixed templates.
    """

    answerability = llm_context.get("answerability", {})
    blocking = answerability.get("blocking_next_step", "")
    usable = llm_context.get("usable_evidence", [])
    unparsed = llm_context.get("unparsed_sources", [])
    readable = llm_context.get("readable_summaries", [])
    unusable = llm_context.get("unusable_parsed_sources", [])
    pending_jobs = llm_context.get("pending_jobs", [])
    failed_jobs = llm_context.get("failed_jobs", [])
    turn_gate = llm_context.get("turn_gate_decision", {}) or {}
    if _is_repo_failure_question(user_input) and failed_jobs:
        return _job_failure_fallback(blocking, pending_jobs, failed_jobs)
    if _is_parse_failure_question(user_input) and (failed_jobs or unusable):
        return _parse_failure_fallback(blocking, pending_jobs, failed_jobs, unusable)

    if turn_gate.get("contract_action") in {"answer_without_contract_update", "ask_clarifying_question"}:
        if api_key:
            return _llm_reply(llm_context, user_input, api_key, provider_url, on_delta=on_delta)
        return "answer", _non_contract_fallback(turn_gate)

    if api_key:
        return _llm_reply(llm_context, user_input, api_key, provider_url, on_delta=on_delta)

    return _unified_fallback(blocking, len(unparsed), len(usable), len(readable), pending_jobs, failed_jobs, unusable)


def _llm_reply(
    llm_context: dict[str, Any],
    user_input: str,
    api_key: str,
    provider_url: str,
    *,
    on_delta: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    readable = llm_context.get("readable_summaries", [])
    confirmed = llm_context.get("confirmed_from_user", {})
    contract = llm_context.get("research_intent_contract", {})
    turn_gate = llm_context.get("turn_gate_decision", {})
    pending_jobs = llm_context.get("pending_jobs", [])
    failed_jobs = llm_context.get("failed_jobs", [])
    unusable = llm_context.get("unusable_parsed_sources", [])
    paper_summaries = llm_context.get("paper_reading_summaries", [])
    artifact_manifests = llm_context.get("artifact_manifests", [])
    blocking = llm_context.get("answerability", {}).get("blocking_next_step", "")
    evidence_text = "\n---\n".join(readable[:3]) if readable else "无可用 evidence"
    confirmed_text = "\n".join(f"{k}: {v}" for k, v in confirmed.items()) if confirmed else "无"
    contract_text = _json_text(contract) if contract else "{}"
    turn_gate_text = _json_text(turn_gate) if turn_gate else "{}"

    system = PromptSelector().build_system_prompt_for_v2_component("reply_planner")

    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": f"当前状态: {blocking or 'idle'}"},
        {"role": "system", "content": f"已确认事实:\n{confirmed_text}"},
        {"role": "system", "content": f"TurnGateDecision:\n{turn_gate_text}"},
        {"role": "system", "content": f"ResearchIntentContract draft:\n{contract_text}"},
        {"role": "system", "content": f"可用 evidence:\n{evidence_text}"},
        {"role": "system", "content": f"Paper reading summaries:\n{_json_text(paper_summaries)}"},
        {"role": "system", "content": f"Artifact manifests:\n{_json_text(artifact_manifests)}"},
        {"role": "system", "content": f"不可用解析结果:\n{_json_text(unusable)}"},
        {"role": "system", "content": f"后台 PipelineJobs:\n{_json_text({'pending_jobs': pending_jobs, 'failed_jobs': failed_jobs})}"},
        {"role": "user", "content": user_input},
    ]

    from autoad_researcher.ui.chat_client import call_research_chat
    visible_stream = _VisibleReplyDeltaFilter(on_delta) if on_delta is not None else None
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model="deepseek-v4-flash",
        timeout_s=30,
        on_delta=visible_stream.feed if visible_stream is not None else None,
    )

    if result.get("reply") and not result.get("error"):
        payload = _parse_llm_contract_reply(str(result["reply"]))
        if payload is not None:
            return "answer", _visible_reply_from_llm_payload(payload)
        return "answer", str(result["reply"])

    return _unified_fallback(blocking, 0, 0, 0, pending_jobs, failed_jobs, unusable)


def _unified_fallback(
    blocking: str,
    unparsed_count: int,
    usable_count: int,
    readable_count: int,
    pending_jobs: list[dict[str, Any]] | None = None,
    failed_jobs: list[dict[str, Any]] | None = None,
    unusable_sources: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Evidence-state fallback — no keyword templates."""
    parts = [f"当前状态: {blocking or 'idle'}"]
    pending_jobs = pending_jobs or []
    failed_jobs = failed_jobs or []
    unusable_sources = unusable_sources or []

    if unparsed_count:
        parts.append(f"未解析 source: {unparsed_count}")
    if pending_jobs:
        job_lines = ", ".join(
            f"{j.get('job_type')}({j.get('status')}, {j.get('job_id')})"
            for j in pending_jobs[:3]
        )
        parts.append(f"后台任务: {job_lines}")
        parts.append("这些任务还没有产出 supported evidence；完成前我不能声称已经读完 PDF。")
    if failed_jobs:
        job_lines = ", ".join(
            f"{j.get('job_type')}({j.get('job_id')}): {j.get('error') or 'failed'}"
            for j in failed_jobs[:3]
        )
        parts.append(f"失败任务: {job_lines}")
    if unusable_sources:
        labels = ", ".join(
            str(item.get("user_label") or item.get("source_id"))
            for item in unusable_sources[:3]
        )
        parts.append(f"解析不可用 source: {labels}")
        known_reasons = _known_unusable_reasons(unusable_sources)
        if known_reasons:
            parts.append("已知原因: " + "；".join(known_reasons[:3]))
        else:
            parts.append("当前只知道这些 PDF 没有产出可读 paper.md。")
        parts.append("因此我不能从中提取论文方法或声称看过内容。")
    if usable_count:
        parts.append(f"可用 evidence: {usable_count}")
    if readable_count:
        parts.append(f"可读摘要: {readable_count}")

    parts.append("HF-2 当前只需要确认研究意图，不需要你先设计具体方法或指定要改哪个模块。")
    parts.append("你有没有已有改进想法？没有也可以，后续 experiment agents 会自动探索。")
    parts.append("请先确认主要目标：指标效果、推理速度、显存占用、训练成本、复现跑通，还是稳定性/泛化？")
    parts.append("默认禁止修改测试集、指标定义、数据划分、测试标签和任何标签泄漏；当前执行模式默认 plan_only。")

    return "answer", "\n".join(parts)


def _parse_failure_fallback(
    blocking: str,
    pending_jobs: list[dict[str, Any]] | None = None,
    failed_jobs: list[dict[str, Any]] | None = None,
    unusable_sources: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    parts = [f"当前状态: {blocking or 'idle'}"]
    pending_jobs = pending_jobs or []
    failed_jobs = failed_jobs or []
    unusable_sources = unusable_sources or []

    if failed_jobs:
        job_lines = ", ".join(
            f"{j.get('job_type')}({j.get('job_id')}): {j.get('error') or 'failed'}"
            for j in failed_jobs[:3]
        )
        parts.append(f"失败任务: {job_lines}")
    if pending_jobs:
        job_lines = ", ".join(
            f"{j.get('job_type')}({j.get('status')}, {j.get('job_id')})"
            for j in pending_jobs[:3]
        )
        parts.append(f"仍在运行/排队的任务: {job_lines}")
    if unusable_sources:
        labels = ", ".join(
            str(item.get("user_label") or item.get("source_id"))
            for item in unusable_sources[:3]
        )
        parts.append(f"解析不可用 source: {labels}")
        known_reasons = _known_unusable_reasons(unusable_sources)
        if known_reasons:
            parts.append("已知原因: " + "；".join(known_reasons[:3]))
        else:
            parts.append("当前只知道这些 PDF 没有产出可读 paper.md。")
    parts.append("这些是当前 artifact 里能确认的原因；我不会补充 artifact 之外的猜测。")
    if unusable_sources:
        parts.append("因此这份 PDF 目前不能作为论文方法细节证据。")
    return "answer", "\n".join(parts)


def _job_failure_fallback(
    blocking: str,
    pending_jobs: list[dict[str, Any]] | None = None,
    failed_jobs: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    pending_jobs = pending_jobs or []
    failed_jobs = failed_jobs or []
    repo_failures = [job for job in failed_jobs if str(job.get("job_type") or "") in {"git_clone", "repo_summarize", "repo_analyze"}]
    root_clone_failures = [job for job in repo_failures if str(job.get("job_type") or "") == "git_clone"]
    dependent_failures = [job for job in repo_failures if str(job.get("error") or "").startswith("dependency failed")]
    network_failures = [job for job in root_clone_failures if _looks_like_network_clone_failure(str(job.get("error") or ""))]

    if network_failures:
        parts = ["是的，从当前 job 记录看，主要失败原因是访问 GitHub 时的网络/TLS 传输失败。"]
    elif root_clone_failures:
        parts = ["当前根因在 git clone 阶段，仓库还没有成功拉到本地。"]
    else:
        parts = [f"当前状态: {blocking or 'idle'}"]

    if root_clone_failures:
        root_lines = ", ".join(
            f"{j.get('job_type')}({j.get('job_id')}): {j.get('error') or 'failed'}"
            for j in root_clone_failures[:3]
        )
        parts.append(f"直接失败任务: {root_lines}")
    elif failed_jobs:
        job_lines = ", ".join(
            f"{j.get('job_type')}({j.get('job_id')}): {j.get('error') or 'failed'}"
            for j in failed_jobs[:5]
        )
        parts.append(f"失败任务: {job_lines}")
    if dependent_failures:
        dep_lines = ", ".join(
            f"{j.get('job_type')}({j.get('job_id')}): {j.get('error') or 'failed'}"
            for j in dependent_failures[:3]
        )
        parts.append(f"级联失败任务: {dep_lines}")
    if pending_jobs:
        job_lines = ", ".join(
            f"{j.get('job_type')}({j.get('status')}, {j.get('job_id')})"
            for j in pending_jobs[:3]
        )
        parts.append(f"仍在运行/排队的任务: {job_lines}")
    if network_failures:
        parts.append("这更像是当前环境到 GitHub 的连接不稳定或被中断，不像是仓库不存在。")
        parts.append(
            "下一步更稳的做法是由用户提供一个当前环境可访问的仓库来源："
            "Gitee/GitCode/AtomGit 等镜像 URL，或本地 clone 后打包的 zip/tar。"
            "系统会把这些材料登记、解析、证据化，再继续仓库分析。"
        )
    elif repo_failures:
        parts.append("因此当前仓库还没有成功 clone/analysis，右侧 Evidence 不应把 repo 摘要当作可用证据。")
    return "answer", "\n".join(parts)


def _looks_like_network_clone_failure(error: str) -> bool:
    lowered = error.lower()
    return any(token in lowered for token in (
        "timed_out",
        "timeout",
        "gnutls",
        "tls connection",
        "non-properly terminated",
        "unable to access",
        "failed to connect",
        "connection reset",
        "cloning into",
        "recv error",
        "early eof",
        "curl",
    ))


def _is_parse_failure_question(user_input: str) -> bool:
    text = re.sub(r"\s+", "", str(user_input).strip().lower())
    if not text:
        return False
    return any(token in text for token in ("失败", "报错", "错误", "原因", "为什么", "为啥", "怎么回事"))


def _is_repo_failure_question(user_input: str) -> bool:
    text = re.sub(r"\s+", "", str(user_input).strip().lower())
    if not text:
        return False
    has_failure_signal = any(token in text for token in ("失败", "报错", "错误", "原因", "为什么", "为啥", "怎么回事"))
    has_repo_signal = any(token in text for token in ("clone", "git", "github", "repo", "仓库"))
    return has_failure_signal and has_repo_signal


def _non_contract_fallback(turn_gate: dict[str, Any] | None = None) -> str:
    instruction = _clean_visible_text((turn_gate or {}).get("next_reply_instruction"))
    if instruction:
        return instruction
    return "这句话不会写入研究合同。需要继续推进研究任务时，可以直接告诉我实验目标、数据集、指标、资料链接或仓库。"


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"


def _parse_llm_contract_reply(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = _parse_key_value_contract_reply(stripped)
    return payload if isinstance(payload, dict) else None


def _parse_key_value_contract_reply(text: str) -> dict[str, Any] | None:
    """Parse LLM key/value output when it ignores the requested JSON envelope."""
    payload: dict[str, Any] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is None:
            return
        raw_value = "\n".join(current_lines).strip()
        payload[current_key] = _parse_loose_value(raw_value)
        current_key = None
        current_lines = []

    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if match and match.group(1) in _CONTRACT_REPLY_KEYS:
            flush()
            current_key = match.group(1)
            current_lines = [match.group(2)]
        elif current_key is not None:
            current_lines.append(line)
    flush()
    return payload if "reply_to_user" in payload else None


def _parse_loose_value(value: str) -> Any:
    stripped = value.strip()
    if stripped in {"", "null", "None"}:
        return None
    if stripped in {"true", "True"}:
        return True
    if stripped in {"false", "False"}:
        return False
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _visible_reply_from_llm_payload(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    reply = _clean_visible_text(payload.get("reply_to_user"))
    question = _clean_visible_text(payload.get("next_question"))
    if reply:
        parts.append(reply)
    if question and question != reply:
        parts.append(question)
    if not parts:
        parts.append("我已更新研究意图草稿。请继续补充目标、指标或成功标准。")
    return "\n\n".join(parts)


def _clean_visible_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


class _VisibleReplyDeltaFilter:
    """Forward only user-visible reply_to_user text from streamed control output."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._buffer = ""
        self._emitted = ""

    def feed(self, delta: str) -> None:
        if not delta:
            return
        self._buffer += delta
        visible = _extract_streamable_reply_to_user(self._buffer)
        if visible is None or len(visible) <= len(self._emitted):
            return
        piece = visible[len(self._emitted):]
        self._emitted = visible
        if piece:
            self._emit(piece)


def _extract_streamable_reply_to_user(text: str) -> str | None:
    json_visible = _extract_json_reply_to_user(text)
    if json_visible is not None:
        return json_visible
    return _extract_key_value_reply_to_user(text)


def _extract_json_reply_to_user(text: str) -> str | None:
    match = re.search(r'"reply_to_user"\s*:\s*"', text)
    if not match:
        return None
    return _partial_json_string_value(text, match.end())


def _partial_json_string_value(text: str, start: int) -> str:
    out: list[str] = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return "".join(out)
        if ch == "\\":
            if i + 1 >= len(text):
                break
            nxt = text[i + 1]
            if nxt == "u":
                if i + 5 >= len(text):
                    break
                code = text[i + 2:i + 6]
                try:
                    out.append(chr(int(code, 16)))
                except ValueError:
                    break
                i += 6
                continue
            out.append({
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _extract_key_value_reply_to_user(text: str) -> str | None:
    match = re.search(r"(?m)^reply_to_user\s*:\s*", text)
    if not match:
        return None
    segment = text[match.end():]
    for next_key in re.finditer(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*", segment):
        key = next_key.group(1)
        if key in _CONTRACT_REPLY_KEYS and key != "reply_to_user":
            return segment[:next_key.start()]

    last_newline = segment.rfind("\n")
    if last_newline >= 0:
        tail = segment[last_newline + 1:].lstrip()
        possible_keys = _CONTRACT_REPLY_KEYS - {"reply_to_user"}
        if tail and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tail):
            if any(key.startswith(tail) for key in possible_keys):
                return segment[:last_newline + 1]
    return segment


def _known_unusable_reasons(unusable_sources: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for item in unusable_sources:
        for warning in item.get("warnings") or []:
            if isinstance(warning, str) and warning.strip():
                reasons.append(warning.strip())
        for error in item.get("fatal_errors") or []:
            if isinstance(error, str) and error.strip():
                reasons.append(error.strip())
        for parser_error in item.get("parser_errors") or []:
            if not isinstance(parser_error, dict):
                continue
            parser = str(parser_error.get("parser_name") or "parser")
            error = str(parser_error.get("error") or "").strip()
            if error:
                reasons.append(f"{parser}: {error}")
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped
