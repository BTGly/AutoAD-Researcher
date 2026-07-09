"""Minimal LLM client used only by the Research Chat UI."""

import json
from collections.abc import Callable
from typing import Any

import httpx


def call_research_chat(
    api_key: str,
    provider_base_url: str,
    messages: list[dict[str, str]],
    model: str = "deepseek-chat",
    timeout_s: int = 60,
    on_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Call the LLM provider and return {reply, error} dict.

    The return dict is always safe to display — it never contains raw
    headers, the API key, or full HTTP response bodies.

    The *provider_base_url* is automatically normalised so that a
    trailing ``/v1`` prefix is not duplicated.

    If *on_delta* is provided, the request uses OpenAI-compatible SSE
    streaming and invokes the callback with content deltas while still
    returning the final accumulated reply.
    """
    base = provider_base_url.rstrip("/")
    if base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    if on_delta is not None:
        return _call_research_chat_streaming(url, payload, headers, timeout_s, on_delta)

    try:
        resp = httpx.post(
            url, json=payload, headers=headers,
            timeout=timeout_s,
        )
    except httpx.TimeoutException:
        return {"reply": "", "error": "请求超时（已等待 {} 秒）。请检查网络后重试。".format(timeout_s)}
    except Exception as exc:
        return {"reply": "", "error": "网络错误：{}".format(str(exc)[:200])}

    if resp.status_code == 200:
        try:
            body = resp.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            return {"reply": content.strip(), "error": ""}
        except (KeyError, IndexError, ValueError) as exc:
            return {"reply": "", "error": "解析响应失败：{}".format(str(exc)[:200])}

    try:
        detail = resp.json()
        err_msg = detail.get("error", {}).get("message", "")
    except Exception:
        err_msg = ""
    return {
        "reply": "",
        "error": "API 返回 HTTP {} — {}".format(
            resp.status_code, err_msg or "请检查 API Key 和网络"
        ),
    }


def _call_research_chat_streaming(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout_s: int,
    on_delta: Callable[[str], None],
) -> dict[str, Any]:
    stream_payload = {**payload, "stream": True}
    chunks: list[str] = []

    try:
        with httpx.stream(
            "POST",
            url,
            json=stream_payload,
            headers=headers,
            timeout=timeout_s,
        ) as resp:
            if resp.status_code != 200:
                return _stream_http_error(resp)
            for line in resp.iter_lines():
                data = _sse_data(line)
                if data is None:
                    continue
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    return {"reply": "", "error": "解析流式响应失败：{}".format(str(exc)[:200])}
                delta = _content_delta_from_stream_event(event)
                if delta is None:
                    continue
                chunks.append(delta)
                on_delta(delta)
    except httpx.TimeoutException:
        return {"reply": "", "error": "请求超时（已等待 {} 秒）。请检查网络后重试。".format(timeout_s)}
    except Exception as exc:
        return {"reply": "", "error": "网络错误：{}".format(str(exc)[:200])}

    return {"reply": "".join(chunks).strip(), "error": ""}


def _sse_data(line: str | bytes) -> str | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    stripped = line.strip()
    if not stripped:
        return None
    if not stripped.startswith("data:"):
        return None
    return stripped.removeprefix("data:").strip()


def _content_delta_from_stream_event(event: dict[str, Any]) -> str | None:
    choices = event.get("choices")
    if not isinstance(choices, list):
        return None
    parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    if not parts:
        return None
    return "".join(parts)


def _stream_http_error(resp: httpx.Response) -> dict[str, str]:
    err_msg = ""
    try:
        body = resp.read()
        if isinstance(body, bytes):
            text = body.decode("utf-8", errors="replace")
        else:
            text = str(body)
        detail = json.loads(text)
        if isinstance(detail, dict):
            error = detail.get("error", {})
            if isinstance(error, dict):
                err_msg = str(error.get("message", ""))
    except Exception:
        err_msg = ""
    return {
        "reply": "",
        "error": "API 返回 HTTP {} — {}".format(
            resp.status_code, err_msg or "请检查 API Key 和网络"
        ),
    }
