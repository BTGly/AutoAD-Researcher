"""Regression tests for removing the legacy Streamlit surface."""

from pathlib import Path

from autoad_researcher.server.routes.chat import _load_config_value


def test_streamlit_entrypoints_and_config_are_removed():
    assert not Path("src/autoad_researcher/ui/app.py").exists()
    assert not Path("src/autoad_researcher/ui/app_v2.py").exists()
    assert not Path("scripts/fix_streamlit_cors.sh").exists()
    assert not Path(".streamlit/config.toml").exists()


def test_pyproject_no_longer_depends_on_streamlit():
    source = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "streamlit" not in source.lower()


def test_server_config_fallback_is_not_streamlit_bound(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"api_key": "sk-test", "provider_url": "https://example.test", "model": "m"}', encoding="utf-8")

    import autoad_researcher.server.routes.chat as chat_route

    monkeypatch.setattr(chat_route, "CONFIG_PATH", config_path)

    assert _load_config_value("api_key") == "sk-test"
    assert _load_config_value("provider_url") == "https://example.test"
    assert _load_config_value("model") == "m"
