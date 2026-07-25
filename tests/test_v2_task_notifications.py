from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.conversation_store import (
    append_message,
    load_messages,
)
from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.assistant.v2.job_service import append_pipeline_job, complete_pipeline_job, fail_pipeline_job
from autoad_researcher.assistant.v2.task_notifications import notify_terminal_job
from autoad_researcher.ui.sources import append_source_ref
from autoad_researcher.worker.main import _process_pending_jobs


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


def test_report_chain_only_notifies_for_delivery_and_collapses_dependency_failure(tmp_path: Path):
    run_dir = tmp_path / "run"
    intermediate = append_pipeline_job(
        run_dir, source_id="", job_type="report_facts_assemble", evidence_role="report_artifact",
    )
    intermediate_done = complete_pipeline_job(run_dir, intermediate["job_id"])
    assert notify_terminal_job(run_dir, intermediate_done or intermediate, succeeded=True) is None

    delivery = append_pipeline_job(
        run_dir, source_id="", job_type="report_package", evidence_role="report_artifact",
    )
    delivery_done = complete_pipeline_job(run_dir, delivery["job_id"], outputs=["reports/report_1/report_bundle.zip"])
    assert notify_terminal_job(run_dir, delivery_done or delivery, succeeded=True) is not None

    upstream = append_pipeline_job(
        run_dir, source_id="src_repo", job_type="local_repo_acquire", evidence_role="repo_acquired",
    )
    upstream_failed = fail_pipeline_job(run_dir, upstream["job_id"], error="repository is unavailable")
    assert notify_terminal_job(run_dir, upstream_failed or upstream, succeeded=False, error="repository is unavailable") is not None
    successor = append_pipeline_job(
        run_dir, source_id="src_repo", job_type="repo_summarize", evidence_role="repo_acquired",
        payload={"depends_on": upstream["job_id"]},
    )
    successor_failed = fail_pipeline_job(run_dir, successor["job_id"], error=f"dependency failed: {upstream['job_id']}")
    assert notify_terminal_job(
        run_dir, successor_failed or successor, succeeded=False,
        error=f"dependency failed: {upstream['job_id']}",
    ) is None
    assert len(load_messages(run_dir)) == 2


def test_environment_experiment_and_authorization_failure_have_distinct_task_messages(tmp_path: Path):
    run_dir = tmp_path / "run"
    environment = append_pipeline_job(
        run_dir, source_id="", job_type="experiment_environment_prepare", evidence_role="environment",
    )
    experiment = append_pipeline_job(
        run_dir, source_id="", job_type="experiment_baseline_b_test", evidence_role="attempt",
    )
    environment_done = complete_pipeline_job(run_dir, environment["job_id"], outputs=["environment/ready.json"])
    experiment_failed = fail_pipeline_job(run_dir, experiment["job_id"], error="B_test authorization is required")

    assert notify_terminal_job(run_dir, environment_done or environment, succeeded=True) is not None
    assert notify_terminal_job(
        run_dir, experiment_failed or experiment, succeeded=False, error="B_test authorization is required",
    ) is not None
    messages = load_messages(run_dir)
    assert messages[0]["message_kind"] == "task_result"
    assert "实验环境准备" in messages[0]["content"]
    assert messages[1]["message_kind"] == "task_failure"
    assert "授权或实验前置条件" in messages[1]["content"]


def test_worker_persists_task_notification_before_terminal_job_event(tmp_path: Path):
    run_dir = tmp_path / "run"
    append_pipeline_job(run_dir, source_id="", job_type="unknown_background_job", evidence_role="")

    _process_pending_jobs(run_dir)

    types = [event["type"] for event in load_events_since(run_dir)]
    assert types.index("conversation.message.created") < types.index("job.failed")
    assert load_messages(run_dir)[0]["message_kind"] == "task_failure"


def test_conversation_store_serializes_concurrent_appends(tmp_path: Path):
    run_dir = tmp_path / "run"
    contents = [f"message {index}" for index in range(12)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda content: append_message(run_dir, role="user", content=content), contents))

    messages = load_messages(run_dir)
    assert len(messages) == len(contents)
    assert {message["content"] for message in messages} == set(contents)
