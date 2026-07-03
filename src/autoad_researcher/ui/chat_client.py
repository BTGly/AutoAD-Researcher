"""Minimal LLM client used only by the Research Chat UI."""

from typing import Any

import httpx


def call_research_chat(
    api_key: str,
    provider_base_url: str,
    messages: list[dict[str, str]],
    model: str = "deepseek-chat",
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Call the LLM provider and return {reply, error} dict.

    The return dict is always safe to display — it never contains raw
    headers, the API key, or full HTTP response bodies.
    """
    url = provider_base_url.rstrip("/") + "/v1/chat/completions"
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
