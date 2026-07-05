"""Tests for Round 4 deterministic assistant runtime skeleton."""

from pathlib import Path
from shutil import copytree

from autoad_researcher.assistant.runtime import DeterministicAssistantRuntime, route_user_text


FIXTURE = Path("tests/fixtures/silent_probe_fixture")


def _copy_fixture(tmp_path: Path, run_id: str = "run_known") -> Path:
    run_dir = tmp_path / run_id
    copytree(FIXTURE, run_dir)
    return run_dir


def test_runtime_probe_first_proposes_from_existing_artifacts(tmp_path):
    _copy_fixture(tmp_path, "run_known")
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("run_known", "继续这个异常检测方向")

    assert result.session.mode == "intent_structuring"
    assert result.prompt_id == "assistant.understanding_intent.v1"
    assert "PatchCore" in result.reply
    assert "已有候选 variant" in result.reply
    assert "category" in result.reply
    assert "metric_direction" in result.reply
    assert "算法" in result.reply or "超参数" in result.reply
    assert result.session.task.execution_approved is False
    assert result.session.task.ready_for_pipeline is False


def test_runtime_empty_run_guides_materials_without_long_form(tmp_path):
    (tmp_path / "empty_run").mkdir()
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("empty_run", "我想做异常检测")

    assert result.session.mode == "goal_alignment"
    assert "不让你填长表单" in result.reply
    assert "论文/方法描述或目标代码仓库二选一" in result.reply
    assert "baseline" not in result.reply.lower()


def test_runtime_correction_returns_to_intent_structuring(tmp_path):
    _copy_fixture(tmp_path, "run_known")
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    runtime.handle_user_message("run_known", "继续这个异常检测方向")
    result = runtime.handle_user_message("run_known", "不是，我不是想先复现，我想先明确指标")

    assert result.event.event_type == "user_input"
    assert "correction" in result.event.router_labels
    assert result.session.mode == "intent_structuring"
    assert "我已按你的纠正更新理解" in result.reply
    assert "不决定具体方法或 patch" in result.reply


def test_runtime_unknown_event_fallback_does_not_crash(tmp_path):
    (tmp_path / "empty_run").mkdir()
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("empty_run", "")

    assert result.event.event_type == "unknown"
    assert result.session.mode == "goal_alignment"
    assert "还不能稳定判断" in result.reply
    assert result.violations == []


def test_runtime_persists_session_events_and_transitions(tmp_path):
    _copy_fixture(tmp_path, "run_known")
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("run_known", "继续这个异常检测方向")

    assistant_dir = tmp_path / "run_known" / "assistant"
    assert (assistant_dir / "session.json").is_file()
    assert (assistant_dir / "events.jsonl").is_file()
    assert (assistant_dir / "transitions.jsonl").is_file()
    assert result.session.last_event_id == result.event.event_id


def test_route_user_text_is_coarse_not_behavior_enumeration():
    assert route_user_text("现在到哪了").event_type == "progress_query"
    assert route_user_text("我上传了论文 pdf").event_type == "source_input"
    assert route_user_text("不是，我要改目标").router_labels == ["correction"]
    assert route_user_text("继续这个方向").router_labels == ["goal_update"]
