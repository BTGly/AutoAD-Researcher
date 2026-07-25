from pathlib import Path

from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.conversation_store import (
    append_message,
    load_messages,
)
from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.assistant.v2.job_service import append_pipeline_job, complete_pipeline_job, fail_pipeline_job
from autoad_researcher.assistant.v2.task_notifications import notify_terminal_job
from autoad_researcher.ui.sources import append_source_ref


def test_task_result_is_durable_event_and_idempotent(tmp_path: Path):
    run_dir = tmp_path / "run"
    append_source_ref(
        run_dir, kind="dataset", user_label="MVTec", stored_path=None,
        status="user_provided_not_ingested", source_id="src_dataset",
    )
    job = append_pipeline_job(
        run_dir, source_id="src_dataset", job_type="dataset_manifest",
        evidence_role="dataset_manifest",
    )
    completed = complete_pipeline_job(run_dir, job["job_id"], outputs=["sources/src_dataset/dataset_manifest.json"])

    first = notify_terminal_job(run_dir, completed or job, succeeded=True)
    second = notify_terminal_job(run_dir, completed or job, succeeded=True)

    assert first is not None
    assert second is None
    messages = load_messages(run_dir)
    assert len(messages) == 1
    assert messages[0]["message_kind"] == "task_result"
    assert messages[0]["job_id"] == job["job_id"]
    assert "MVTec" in messages[0]["content"]
    events = load_events_since(run_dir)
    assert [event["type"] for event in events] == ["conversation.message.created"]


def test_task_failure_becomes_context_without_claiming_evidence(tmp_path: Path):
    run_dir = tmp_path / "run"
    job = append_pipeline_job(
        run_dir, source_id="src_missing", job_type="dataset_manifest",
        evidence_role="dataset_manifest",
    )
    failed = fail_pipeline_job(run_dir, job["job_id"], error="dataset path is unavailable")

    notification = notify_terminal_job(
        run_dir, failed or job, succeeded=False, error="dataset path is unavailable"
    )

    assert notification is not None
    context = build_llm_context(run_dir)
    assert context["recent_task_messages"][0]["message_kind"] == "task_failure"
    assert context["answerability"]["can_answer"] is False


def test_conversation_store_keeps_legacy_and_structured_entries(tmp_path: Path):
    run_dir = tmp_path / "run"
    append_message(run_dir, role="user", content="inspect this repository")
    append_message(
        run_dir, role="assistant", content="**仓库调查已完成**",
        message_kind="task_result", notification_key="task-result:job_1", job_id="job_1",
    )

    messages = load_messages(run_dir)
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["message_kind"] == "chat_reply"
    assert messages[1]["message_kind"] == "task_result"
