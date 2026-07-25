"""Turn durable pipeline Job terminal states into concise conversation updates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.conversation_store import append_message
from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.ui.sources import load_source_registry

_CHAIN_FINAL_TYPES = {
    "web_search", "repo_summarize", "dataset_manifest", "web_markitdown",
    "paper_parse", "paper_parse_mineru", "paper_parse_markitdown",
    "paper_summarize", "document_markitdown", "archive_unpack_classify",
    "report_package", "report_render_pdf",
}
_CHAIN_INTERMEDIATE_TYPES = {
    "git_clone", "local_repo_acquire", "local_repo_unpack", "web_fetch",
    "report_snapshot_build", "report_facts_assemble", "report_narrative_generate",
    "report_validate", "report_render_html",
}


def notify_terminal_job(run_dir: Path, job: dict[str, Any], *, succeeded: bool, error: str = "") -> dict[str, Any] | None:
    """Persist and emit one visible task message when this terminal state matters."""

    job_type = str(job.get("job_type") or "任务")
    if succeeded and job_type in _CHAIN_INTERMEDIATE_TYPES:
        return None
    if succeeded and job_type not in _CHAIN_FINAL_TYPES and not _is_high_level_job(job_type):
        return None
    if not succeeded and _is_dependency_failure(error) and _has_failed_dependency(run_dir, job):
        return None

    source_id = str(job.get("source_id") or "")
    outputs = [str(item) for item in job.get("outputs") or [] if isinstance(item, str)]
    evidence_ids = _related_evidence_ids(run_dir, source_id=source_id, outputs=outputs)
    title = _job_label(job_type)
    source_label = _source_label(run_dir, source_id)
    if succeeded:
        content = f"**{title}已完成**"
        if source_label:
            content += f"：{source_label}"
        if outputs:
            content += f"。已生成 {len(outputs)} 个产物，可继续基于这些资料对话或进入下一步准备。"
        else:
            content += "。状态已同步到研究上下文。"
        kind = "task_result"
        notification_key = f"task-result:{job.get('job_id', '')}"
    else:
        detail = (error or str(job.get("error") or "任务失败")).strip()[:500]
        content = f"**{title}未完成**"
        if source_label:
            content += f"：{source_label}"
        content += f"。原因：{detail}。{_failure_next_step(job_type, detail)}"
        kind = "task_failure"
        notification_key = f"task-failure:{job.get('job_id', '')}"

    message, created = append_message(
        run_dir, role="assistant", content=content, message_kind=kind,
        notification_key=notification_key, job_id=str(job.get("job_id") or ""),
        source_id=source_id or None, artifact_paths=outputs, evidence_ids=evidence_ids,
        error=detail if not succeeded else None,
    )
    if not created:
        return None
    event = append_event(run_dir, "conversation.message.created", message)
    return {**message, "event_id": event.get("event_id"), "created_at": event.get("created_at")}


def _is_high_level_job(job_type: str) -> bool:
    return job_type.startswith("experiment_")


def _is_dependency_failure(error: str) -> bool:
    return error.startswith("dependency failed:")


def _has_failed_dependency(run_dir: Path, job: dict[str, Any]) -> bool:
    payload = job.get("payload")
    dependency = payload.get("depends_on") if isinstance(payload, dict) else None
    if not dependency:
        return False
    from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
    return any(item.get("job_id") == dependency and item.get("status") == "failed" for item in load_pipeline_jobs(run_dir))


def _source_label(run_dir: Path, source_id: str) -> str:
    if not source_id:
        return ""
    for source in load_source_registry(run_dir).get("sources", []):
        if source.get("source_id") == source_id:
            return str(source.get("user_label") or source.get("original_reference") or source_id)
    return source_id


def _job_label(job_type: str) -> str:
    return {
        "web_search": "资料检索",
        "repo_summarize": "仓库调查", "dataset_manifest": "数据集清单生成",
        "paper_parse_mineru": "论文解析", "document_markitdown": "文档解析",
        "web_markitdown": "网页解析", "archive_unpack_classify": "资料包解析",
        "experiment_environment_prepare": "实验环境准备",
        "report_package": "报告打包", "report_render_pdf": "报告 PDF 生成",
    }.get(job_type, job_type.replace("_", " "))


def _related_evidence_ids(run_dir: Path, *, source_id: str, outputs: list[str]) -> list[str]:
    if not source_id:
        return []
    output_set = set(outputs)
    return [
        str(item["evidence_id"])
        for item in load_usable_evidence(run_dir)
        if item.get("source_id") == source_id
        and isinstance(item.get("evidence_id"), str)
        and (not output_set or item.get("artifact_path") in output_set)
    ]


def _failure_next_step(job_type: str, error: str) -> str:
    lowered = error.lower()
    if "b_test" in lowered or "authorization" in lowered or "授权" in error:
        return "需要先完成相应授权或实验前置条件。"
    if job_type.startswith("report_"):
        return "可在报告状态中修正问题后重试该步骤。"
    if job_type.startswith("experiment_"):
        return "请检查实验环境、授权和前置条件后再继续。"
    return "可在资料面板检查原因并按需要重试。"
