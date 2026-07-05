"""Regression tests for Streamlit API key session-state handling."""

from pathlib import Path


APP_SOURCE = Path("src/autoad_researcher/ui/app.py")


def test_password_widget_does_not_bind_business_api_key_state():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'key="api_key"' not in source
    assert 'st.session_state.get("api_key"' not in source
    assert "_API_KEY_WIDGET_KEY" in source
    assert "_API_KEY_STATE_KEY" in source


def test_preflight_uses_persisted_raw_api_key_state():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'api_key = st.session_state.get(_API_KEY_STATE_KEY, "")' in source
    assert "api_key=api_key" in source
    assert "st.session_state[_API_KEY_STATE_KEY] = api_key_val" in source


def test_ui_can_load_provider_api_key_from_environment_or_dotenv():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "PROVIDER_API_KEY_ENV" in source
    assert "load_api_key_from_environment" in source
    assert "_ensure_api_key_loaded()" in source
    assert "LOCAL_ENV_PATH" in source


def test_ui_can_save_manually_entered_key_to_local_dotenv():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "save_api_key_to_local_env" in source
    assert "保存到本地 .env，下次自动加载" in source
    assert "mask_api_key" in source
    assert "os.environ[PROVIDER_API_KEY_ENV]" in source


def test_terminal_reproduction_commands_source_dotenv_without_prompting_for_key():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "[ -f .env ] && set -a && source .env && set +a" in source
    assert "Set DEEPSEEK_API_KEY in .env or environment" in source
    assert "read -s -p" not in source
