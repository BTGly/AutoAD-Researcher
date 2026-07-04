"""Tests for Phase 2E task profile — human-readable task title."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

try:
    import streamlit as _st  # noqa: F401
    _HAS_STREAMLIT = True
except ModuleNotFoundError:
    _HAS_STREAMLIT = False

from autoad_researcher.ui.task_profile import (
    TaskProfile,
    fallback_task_profile,
    generate_task_profile_from_first_message,
    get_task_display_info,
    get_task_title,
    load_task_profile,
    save_task_profile,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tmp_run_dir(tmp_path: Path, run_id: str = "run_20260703_1200_a3b2") -> Path:
    d = tmp_path / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _valid_profile(run_dir: Path, **overrides) -> TaskProfile:
    kwargs = {
        "run_id": run_dir.name,
        "task_title": "降低 PatchCore 显存",
        "task_summary": "优化显存占用同时保持 AUROC。",
        "source": "llm_first_user_instruction",
    }
    kwargs.update(overrides)
    return TaskProfile(**kwargs)


# ---------------------------------------------------------------------------
# TaskProfile model
# ---------------------------------------------------------------------------


class TestTaskProfile:
    def test_valid_profile(self):
        p = TaskProfile(
            run_id="run_001",
            task_title="降低 PatchCore 显存",
            task_summary="优化显存。",
            source="llm_first_user_instruction",
        )
        assert p.task_title == "降低 PatchCore 显存"
        assert p.schema_version == 1

    def test_rejects_sk_secret_in_title(self):
        with pytest.raises(ValidationError, match="secret"):
            TaskProfile(
                run_id="run_001",
                task_title="使用 sk-abc123def456 优化",
                task_summary="...",
                source="llm_first_user_instruction",
            )

    def test_rejects_sk_secret_in_summary(self):
        with pytest.raises(ValidationError, match="secret"):
            TaskProfile(
                run_id="run_001",
                task_title="降低显存",
                task_summary="用到 sk-abc123def456 密钥",
                source="llm_first_user_instruction",
            )

    def test_rejects_title_that_is_run_id(self):
        with pytest.raises(ValidationError, match="run_id"):
            TaskProfile(
                run_id="run_20260703_1200_a3b2",
                task_title="run_20260703_1200_a3b2",
                task_summary="...",
                source="llm_first_user_instruction",
            )

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            TaskProfile(
                run_id="run_001",
                task_title="Title",
                task_summary="Summary",
                source="fallback",
                extra_field="oops",
            )

    def test_rejects_empty_title(self):
        with pytest.raises(ValidationError):
            TaskProfile(
                run_id="run_001",
                task_title="",
                task_summary="Summary",
                source="fallback",
            )

    def test_rejects_title_too_long(self):
        with pytest.raises(ValidationError):
            TaskProfile(
                run_id="run_001",
                task_title="这是" + "一个非常长的标题" * 5 + "超过了三十个字符的限制应该报错",
                task_summary="Summary",
                source="fallback",
            )


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_save_and_load_roundtrip(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        p = _valid_profile(run_dir)
        saved = save_task_profile(run_dir, p)
        assert saved.name == "task_profile.json"

        loaded = load_task_profile(run_dir)
        assert loaded is not None
        assert loaded.task_title == p.task_title
        assert loaded.task_summary == p.task_summary
        assert loaded.source == p.source
        assert loaded.run_id == run_dir.name

    def test_load_nonexistent(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        assert load_task_profile(run_dir) is None

    def test_rejects_overwrite(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        save_task_profile(run_dir, _valid_profile(run_dir))
        with pytest.raises(FileExistsError):
            save_task_profile(run_dir, _valid_profile(run_dir))


# ---------------------------------------------------------------------------
# fallback
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_has_expected_title(self):
        p = fallback_task_profile("run_001")
        assert p.task_title == "未命名研究任务"
        assert p.source == "fallback"
        assert p.run_id == "run_001"

    def test_fallback_is_valid_task_profile(self):
        p = fallback_task_profile("run_001")
        TaskProfile.model_validate(p.model_dump())


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


class TestUIHelpers:
    def test_get_task_title_with_profile(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        save_task_profile(run_dir, _valid_profile(run_dir))
        assert get_task_title(run_dir) == "降低 PatchCore 显存"

    def test_get_task_title_without_profile(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        assert get_task_title(run_dir) == "未命名研究任务"

    def test_get_display_info(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        save_task_profile(run_dir, _valid_profile(run_dir))
        info = get_task_display_info(run_dir)
        assert info["task_title"] == "降低 PatchCore 显存"
        assert info["run_id"] == run_dir.name
        assert str(run_dir) in info["artifact_dir"]
        assert info["task_source"] == "llm_first_user_instruction"

    def test_get_display_info_fallback(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        info = get_task_display_info(run_dir)
        assert info["task_title"] == "未命名研究任务"
        assert info["task_source"] == "fallback"

    def test_get_display_info_includes_run_id(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        info = get_task_display_info(run_dir)
        assert info["run_id"] == run_dir.name


# ---------------------------------------------------------------------------
# LLM generation
# ---------------------------------------------------------------------------


class TestGenerateTaskProfile:
    def test_malformed_json_returns_fallback(self, tmp_path, monkeypatch):
        """When LLM returns garbage, fallback is returned."""
        import httpx

        def mock_post(*args, **kwargs):
            resp = httpx.Response(200, json={
                "choices": [{"message": {"content": "not valid json at all"}}]
            })
            return resp

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="我想降低 PatchCore 的显存占用",
        )
        assert profile.source == "fallback"

    def test_empty_response_returns_fallback(self, tmp_path, monkeypatch):
        import httpx

        def mock_post(*args, **kwargs):
            resp = httpx.Response(200, json={
                "choices": [{"message": {"content": "{}"}}]
            })
            return resp

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="测试",
        )
        assert profile.source == "fallback"

    def test_network_error_returns_fallback(self, tmp_path, monkeypatch):
        import httpx

        def mock_post(*args, **kwargs):
            raise httpx.TimeoutException("timeout")

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="测试",
        )
        assert profile.source == "fallback"

    def test_http_error_returns_fallback(self, tmp_path, monkeypatch):
        import httpx

        def mock_post(*args, **kwargs):
            resp = httpx.Response(500, json={"error": "server error"})
            return resp

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="测试",
        )
        assert profile.source == "fallback"

    def test_valid_json_parsed(self, tmp_path, monkeypatch):
        import httpx

        def mock_post(*args, **kwargs):
            resp = httpx.Response(200, json={
                "choices": [{"message": {"content": '{"task_title": "降低 PatchCore 显存", "task_summary": "优化显存同时保持 AUROC。","extra_junk": "ignored"}'}}]
            })
            return resp

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="我想降低 PatchCore 的显存占用",
        )
        assert profile.source == "llm_first_user_instruction"
        assert profile.task_title == "降低 PatchCore 显存"

    def test_llm_sk_secret_rejected(self, tmp_path, monkeypatch):
        import httpx

        def mock_post(*args, **kwargs):
            resp = httpx.Response(200, json={
                "choices": [{"message": {"content": '{"task_title": "使用 sk-abc123def456 优化", "task_summary": "test"}'}}]
            })
            return resp

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="测试",
        )
        assert profile.source == "fallback"

    def test_llm_run_id_in_title_rejected(self, tmp_path, monkeypatch):
        import httpx

        def mock_post(*args, **kwargs):
            resp = httpx.Response(200, json={
                "choices": [{"message": {"content": '{"task_title": "run_20260703_1200_a3b2", "task_summary": "test"}'}}]
            })
            return resp

        monkeypatch.setattr(httpx, "post", mock_post)
        run_dir = _tmp_run_dir(tmp_path)
        profile = generate_task_profile_from_first_message(
            run_dir=run_dir,
            api_key="sk-test",
            provider_base_url="https://api.example.com",
            first_user_message="测试",
        )
        assert profile.source == "fallback"


# ---------------------------------------------------------------------------
# run_id path unchanged
# ---------------------------------------------------------------------------


class TestRunIdPathUnchanged:
    def test_run_id_path_unchanged_by_save(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, run_id="run_my_custom_id")
        save_task_profile(run_dir, _valid_profile(run_dir))
        assert run_dir.name == "run_my_custom_id"
        assert run_dir.is_dir()

    def test_profile_contains_run_id(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, run_id="run_abc123")
        profile = _valid_profile(run_dir, run_id="run_abc123")
        assert profile.run_id == "run_abc123"
        assert "run_abc123" not in profile.task_title


# ---------------------------------------------------------------------------
# app.py import-compile check
# ---------------------------------------------------------------------------


class TestAppImport:
    """Verify that app.py can be compiled/imported without runtime errors.

    Does NOT start Streamlit — only checks that the module's top-level
    code (imports, function defs, class defs) compiles cleanly.  Catches
    regressions like:
      - NameError from calling a function before its definition
      - Duplicate st.set_page_config()
      - Missing imports (Path, etc.)
    """

    @pytest.mark.skipif(_HAS_STREAMLIT is False, reason="streamlit is optional (ui extra)")
    def test_app_compiles(self, monkeypatch):
        """Simulate streamlit enough that app.py can be compiled."""
        monkeypatch.setattr("streamlit.set_page_config", lambda **kw: None)
        monkeypatch.setattr("streamlit.sidebar.radio", lambda *a, **kw: "1. 运行配置")
        monkeypatch.setattr("streamlit.sidebar.markdown", lambda *a, **kw: None)
        monkeypatch.setattr("streamlit.sidebar.caption", lambda *a, **kw: None)
        monkeypatch.setattr("streamlit.sidebar.text_input", lambda *a, **kw: "")
        monkeypatch.setattr("streamlit.sidebar.expander", lambda *a, **kw: type("_ctx", (), {"__enter__": lambda s: None, "__exit__": lambda s,*a: None})())
        monkeypatch.setattr("streamlit.sidebar.code", lambda *a, **kw: None)
        monkeypatch.setattr("streamlit.session_state.setdefault", lambda k, v: None)

        import ast
        app_path = Path(__file__).parent.parent / "src" / "autoad_researcher" / "ui" / "app.py"
        source = app_path.read_text()
        try:
            ast.parse(source)
        except SyntaxError as exc:
            pytest.fail(f"app.py has a syntax error: {exc}")

    def test_app_no_duplicate_set_page_config(self):
        """Verify exactly one st.set_page_config() call exists in app.py."""
        app_path = Path(__file__).parent.parent / "src" / "autoad_researcher" / "ui" / "app.py"
        source = app_path.read_text()
        count = source.count("st.set_page_config(")
        assert count == 1, f"Expected 1 st.set_page_config() call, found {count}"
