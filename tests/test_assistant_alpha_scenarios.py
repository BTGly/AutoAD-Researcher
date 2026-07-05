"""Round 7 deterministic Alpha scenario regression tests."""

import json
from pathlib import Path
from shutil import copytree

from autoad_researcher.assistant.probe import silent_probe
from autoad_researcher.assistant.runtime import DeterministicAssistantRuntime
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.assistant.task_artifacts import AssistantTaskArtifactService, TASK_CONFIRMED_JSON_ARTIFACT


FIXTURE = Path("tests/fixtures/silent_probe_fixture")


def _copy_fixture(tmp_path: Path, run_id: str = "run_known") -> None:
    copytree(FIXTURE, tmp_path / run_id)


def test_alpha_existing_artifacts_fast_path_is_probe_first(tmp_path):
    _copy_fixture(tmp_path)
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("run_known", "继续这个异常检测方向")

    assert "PatchCore" in result.reply
    assert "baseline" in result.reply
    assert "已有候选 variant" in result.reply
    assert "category" in result.reply
    assert "metric_direction" in result.reply
    assert "你要用什么 baseline" not in result.reply
    assert result.session.mode == "intent_structuring"


def test_alpha_from_zero_path_guides_minimal_materials(tmp_path):
    (tmp_path / "empty_run").mkdir()
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("empty_run", "我想做异常检测，但还没有整理材料")

    assert "不让你填长表单" in result.reply
    assert "论文/方法描述或目标代码仓库二选一" in result.reply
    assert "你要用什么 baseline" not in result.reply
    assert "你预算多少" not in result.reply


def test_alpha_user_correction_updates_direction_quickly(tmp_path):
    _copy_fixture(tmp_path)
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    runtime.handle_user_message("run_known", "继续这个异常检测方向")
    corrected = runtime.handle_user_message("run_known", "不是，我不是想先复现，我想先明确评价指标")

    assert corrected.session.mode == "intent_structuring"
    assert "我已按你的纠正更新理解" in corrected.reply
    assert "先明确评价指标" in corrected.reply


def test_alpha_blocking_gap_path_does_not_infer_missing_category_or_direction(tmp_path):
    _copy_fixture(tmp_path)

    what = silent_probe("run_known", runs_root=tmp_path)

    assert "category" in what.missing_fields
    assert "metric_direction" in what.missing_fields
    assert what.dataset is None
    assert what.primary_metric is None


def test_alpha_progress_question_hides_raw_paths(tmp_path):
    _copy_fixture(tmp_path)
    runtime = DeterministicAssistantRuntime(runs_root=str(tmp_path))

    result = runtime.handle_user_message("run_known", "现在到哪了")

    assert result.event.event_type == "progress_query"
    assert result.prompt_id == "assistant.progress_digest.v1"
    assert "baseline_architecture_contract.json" not in result.reply
    assert "runs/" not in result.reply
    assert "raw" not in result.reply.lower()


def test_alpha_task_confirmation_writes_confirmed_task_without_execution_approval(tmp_path):
    _copy_fixture(tmp_path)
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    what = silent_probe("run_known", runs_root=tmp_path)
    session = AutoADAssistantSession(session_id="s1", run_id="run_known", mode="intent_structuring")

    draft, draft_session = service.create_research_task_draft(
        session=session,
        what_we_know=what,
        metric_command="python eval.py --metric image_auroc",
        metric_name="image_auroc",
        metric_direction="maximize",
        baseline="PatchCore",
        dataset="MVTec AD",
        constraints=["不改 eval 脚本"],
        user_idea="提升异常检测指标",
    )
    confirmed, confirmed_session = service.confirm_research_task(
        session=draft_session,
        draft=draft,
        confirmation_evidence_id="ev_user_confirmed_alpha",
    )

    confirmed_path = tmp_path / "run_known" / TASK_CONFIRMED_JSON_ARTIFACT
    payload = json.loads(confirmed_path.read_text())
    assert confirmed.confirmation == "confirmed"
    assert payload["confirmation"] == "confirmed"
    assert confirmed_session.task.ready_for_pipeline is True
    assert confirmed_session.task.execution_approved is False
    assert "method" not in payload
    assert "variant_choice" not in payload
