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
