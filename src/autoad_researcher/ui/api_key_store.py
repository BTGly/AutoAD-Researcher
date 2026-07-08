"""Local API key loading/saving helpers for the research UI.

The value is never logged or committed.  The project-local `.env` file is
already gitignored and is the intended place for local provider credentials.
"""

from __future__ import annotations

import os
from pathlib import Path


PROVIDER_API_KEY_ENV = "DEEPSEEK_API_KEY"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOCAL_ENV_PATH = PROJECT_ROOT / ".env"


def strip_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def mask_api_key(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "未设置"
    suffix = stripped[-4:] if len(stripped) >= 4 else stripped
    return f"****{suffix}"


def read_api_key_from_local_env(path: Path = LOCAL_ENV_PATH) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() == PROVIDER_API_KEY_ENV:
            return strip_env_value(value)
    return ""


def load_api_key_from_environment(path: Path = LOCAL_ENV_PATH) -> tuple[str, str]:
    env_value = os.environ.get(PROVIDER_API_KEY_ENV, "").strip()
    if env_value:
        return env_value, f"环境变量 {PROVIDER_API_KEY_ENV}"

    local_value = read_api_key_from_local_env(path)
    if local_value:
        return local_value, "本地 .env"

    return "", ""


def save_api_key_to_local_env(api_key: str, path: Path = LOCAL_ENV_PATH) -> None:
    value = api_key.strip()
    if not value:
        raise ValueError("API Key 不能为空")
    if "\n" in value or "\r" in value:
        raise ValueError("API Key 不能包含换行")

    try:
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing_lines = []

    updated_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        stripped = line.strip()
        key, separator, _value = stripped.partition("=")
        if separator and not stripped.startswith("#") and key.strip() == PROVIDER_API_KEY_ENV:
            updated_lines.append(f"{PROVIDER_API_KEY_ENV}={value}")
            replaced = True
        else:
            updated_lines.append(line)

    if not replaced:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"{PROVIDER_API_KEY_ENV}={value}")

    path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
