"""Tests for local UI API key storage helpers."""

import pytest

from autoad_researcher.ui.api_key_store import (
    PROVIDER_API_KEY_ENV,
    load_api_key_from_environment,
    mask_api_key,
    read_api_key_from_local_env,
    save_api_key_to_local_env,
)


def test_read_api_key_from_local_env_strips_quotes(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f'{PROVIDER_API_KEY_ENV}="sk-test-1234"\n', encoding="utf-8")

    assert read_api_key_from_local_env(env_path) == "sk-test-1234"


def test_load_api_key_prefers_process_environment(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(f"{PROVIDER_API_KEY_ENV}=sk-from-file\n", encoding="utf-8")
    monkeypatch.setenv(PROVIDER_API_KEY_ENV, "sk-from-env")

    value, source = load_api_key_from_environment(env_path)

    assert value == "sk-from-env"
    assert PROVIDER_API_KEY_ENV in source


def test_save_api_key_to_local_env_replaces_existing_key_and_preserves_other_lines(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join([
            "# local config",
            "GITHUB_TOKEN=ghp_xxx",
            f"{PROVIDER_API_KEY_ENV}=sk-old-ba28",
            "AUTOAD_OTHER=value",
        ]) + "\n",
        encoding="utf-8",
    )

    save_api_key_to_local_env("sk-new-good", env_path)

    text = env_path.read_text(encoding="utf-8")
    assert f"{PROVIDER_API_KEY_ENV}=sk-new-good" in text
    assert f"{PROVIDER_API_KEY_ENV}=sk-old-ba28" not in text
    assert "GITHUB_TOKEN=ghp_xxx" in text
    assert "AUTOAD_OTHER=value" in text


def test_save_api_key_to_local_env_appends_when_missing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("AUTOAD_OTHER=value\n", encoding="utf-8")

    save_api_key_to_local_env("sk-added", env_path)

    assert f"{PROVIDER_API_KEY_ENV}=sk-added" in env_path.read_text(encoding="utf-8")


def test_save_api_key_rejects_empty_or_multiline(tmp_path):
    with pytest.raises(ValueError, match="不能为空"):
        save_api_key_to_local_env("  ", tmp_path / ".env")
    with pytest.raises(ValueError, match="不能包含换行"):
        save_api_key_to_local_env("sk-one\nsk-two", tmp_path / ".env")


def test_mask_api_key_only_exposes_suffix():
    assert mask_api_key("sk-abcdef-ba28") == "****ba28"
    assert mask_api_key("") == "未设置"
