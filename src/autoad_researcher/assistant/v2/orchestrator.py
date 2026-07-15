"""Single-call V2 research dialogue orchestrator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from autoad_researcher.assistant.v2.context_builder import build_llm_context
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import append_pipeline_job, load_pipeline_jobs
from autoad_researcher.assistant.v2.research_dialogue_agent import ResearchDialogueAgent
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.source_actions import (
    SourceActionPlan,
    plan_explicit_source_actions,
)
from autoad_researcher.assistant.v2.source_service import register_source_intake
from autoad_researcher.ui.sources import load_source_registry, remove_source


@dataclass
class OrchestratorResult:
    reply: str = ""
    reply_kind: str = "answer"
    created_sources: list[dict[str, Any]] = field(default_factory=list)
    created_jobs: list[dict[str, Any]] = field(default_factory=list)
    evidence_used: list[dict[str, Any]] = field(default_factory=list)
    answerability: dict[str, Any] = field(default_factory=dict)
    intent_summary: dict[str, Any] = field(default_factory=dict)


class ResearchOrchestratorV2:
    """Run deterministic material intake, then one research dialogue call."""

    @classmethod
    def handle(
        cls,
        run_dir: Path,
        *,
        user_input: str,
        attachments: list[str] | None = None,
        transcript_tail: list[dict[str, Any]] | None = None,
        api_key: str = "",
        provider_url: str = "",
        on_reply_delta: Callable[[str], None] | None = None,
    ) -> OrchestratorResult:
        user_input = user_input.strip()
        if not user_input:
            return OrchestratorResult(reply="请输入问题。")

        created_sources: list[dict[str, Any]] = []
        created_jobs: list[dict[str, Any]] = []
        removed_source = _maybe_remove_latest_source(run_dir, user_input)

        source_plan = plan_explicit_source_actions(
            user_input=user_input,
            attachments=attachments,
        )
        if removed_source is None and source_plan is not None:
            created_sources, created_jobs = _execute_source_action_plan(
                run_dir,
                user_input,
                source_plan,
            )
        target_job = _queue_explicit_repository_target(
            run_dir,
            user_input,
            created_sources=created_sources,
            created_jobs=created_jobs,
        )
        if target_job is not None:
            created_jobs.append(target_job)

        context = build_llm_context(run_dir, transcript_tail=transcript_tail)
        context["current_turn_material_actions"] = {
            "created_sources": created_sources,
            "created_jobs": created_jobs,
            "removed_source": removed_source,
        }
        previous = load_research_intent_summary(run_dir)
        dialogue = ResearchDialogueAgent.respond(
            user_input=user_input,
            evidence_state=context,
            last_summary=previous,
            transcript_tail=transcript_tail,
            api_key=api_key,
            provider_url=provider_url,
            on_reply_delta=on_reply_delta,
        )
        if dialogue.should_persist:
            save_research_intent_summary(run_dir, dialogue.summary)

        return OrchestratorResult(
            reply=dialogue.visible_reply(),
            created_sources=created_sources,
            created_jobs=created_jobs,
            evidence_used=context.get("usable_evidence", []),
            answerability=context.get("answerability", {}),
            intent_summary=dialogue.summary.model_dump(mode="json"),
        )


def _maybe_remove_latest_source(run_dir: Path, user_input: str) -> dict[str, Any] | None:
    if not _is_source_removal_request(user_input):
        return None
    sources = _source_registry_sources(run_dir)
    if not sources:
        return None
    latest = max(sources, key=lambda item: str(item.get("created_at") or ""))
    source_id = str(latest.get("source_id") or "")
    if not source_id:
        return None
    removed = remove_source(run_dir, source_id, reason="user_rejected_latest_upload")
    if removed is None:
        return None
    append_event(run_dir, "source.deleted", {"source_id": source_id})
    append_event(run_dir, "evidence.updated", {"source_id": source_id})
    append_event(run_dir, "toast.success", {"message": "已删除误上传资料"})
    return removed


def _is_source_removal_request(text: str) -> bool:
    lowered = text.strip().lower()
    wrong = any(
        token in lowered
        for token in ("上传错", "传错", "不是我们要的", "不是我要的", "不相关", "删掉", "删除")
    )
    source = any(
        token in lowered
        for token in ("资料", "文件", "上传", "source", "evidence", "这个", "刚才")
    )
    return wrong and source


def _execute_source_action_plan(
    run_dir: Path,
    user_input: str,
    source_plan: SourceActionPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created_sources: list[dict[str, Any]] = []
    created_jobs: list[dict[str, Any]] = []
    registered_urls: dict[str, dict[str, Any]] = {}

    for action in source_plan.actions:
        if action.requires_confirmation or action.action_type == "answer_only":
            continue
        if action.action_type not in {"register_webpage", "register_github_repo"}:
            continue
        if not action.source_url:
            continue
        source_kind = (
            "github_repo"
            if action.action_type == "register_github_repo"
            else "webpage"
        )
        source = registered_urls.get(action.source_url)
        if source is None:
            source = register_source_intake(
                run_dir,
                user_input=user_input,
                source_kind=source_kind,
                source_url=action.source_url,
            )
            registered_urls[action.source_url] = source
            created_sources.append(source)
        source_id = str(source.get("source_id", ""))
        if source_kind == "github_repo":
            clone = append_pipeline_job(
                run_dir,
                source_id=source_id,
                job_type="git_clone",
                evidence_role="candidate_source_only",
                payload={"source_action": action.model_dump(mode="json")},
            )
            created_jobs.append(clone)
            created_jobs.append(
                append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="repo_summarize",
                    evidence_role="repo_acquired",
                    payload={
                        "depends_on": clone.get("job_id"),
                        "source_action": action.model_dump(mode="json"),
                    },
                )
            )
        else:
            fetch = append_pipeline_job(
                run_dir,
                source_id=source_id,
                job_type="web_fetch",
                evidence_role="source_acquired_unparsed",
                payload={"source_action": action.model_dump(mode="json")},
            )
            created_jobs.append(fetch)
            created_jobs.append(
                append_pipeline_job(
                    run_dir,
                    source_id=source_id,
                    job_type="web_markitdown",
                    evidence_role="parsed_web_evidence",
                    payload={
                        "depends_on": fetch.get("job_id"),
                        "source_action": action.model_dump(mode="json"),
                    },
                )
            )
    return created_sources, created_jobs


def _source_registry_sources(run_dir: Path) -> list[dict[str, Any]]:
    sources = load_source_registry(run_dir).get("sources", [])
    return [item for item in sources if isinstance(item, dict)]


def _queue_explicit_repository_target(
    run_dir: Path,
    user_input: str,
    *,
    created_sources: list[dict[str, Any]],
    created_jobs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    target = _parse_explicit_repository_target(user_input)
    if target is None:
        return None
    repository_sources = [
        source
        for source in created_sources
        if source.get("kind") in {"github_repo", "local_repo"}
    ]
    if not repository_sources:
        repository_sources = [
            source
            for source in _source_registry_sources(run_dir)
            if source.get("kind") in {"github_repo", "local_repo"}
        ]
    if not repository_sources:
        return None
    source = max(repository_sources, key=lambda item: str(item.get("created_at") or ""))
    source_id = str(source.get("source_id") or "")
    if not source_id:
        return None
    payload = {"repository_target": target}
    for job in load_pipeline_jobs(run_dir):
        if (
            job.get("source_id") == source_id
            and job.get("job_type") in {"repo_analyze", "repo_summarize"}
            and (job.get("payload") or {}).get("repository_target") == target
        ):
            return None
    clone = next(
        (
            job
            for job in created_jobs
            if job.get("source_id") == source_id and job.get("job_type") == "git_clone"
        ),
        None,
    )
    if clone is not None:
        payload["depends_on"] = clone.get("job_id")
    return append_pipeline_job(
        run_dir,
        source_id=source_id,
        job_type="repo_analyze",
        evidence_role="repo_acquired",
        payload=payload,
    )


def _parse_explicit_repository_target(user_input: str) -> dict[str, int] | None:
    level_match = re.search(r"(?<![A-Za-z0-9_])level\s*=\s*([0-9]+)", user_input)
    problem_match = re.search(r"(?<![A-Za-z0-9_])problem_id\s*=\s*([0-9]+)", user_input)
    if level_match is None or problem_match is None:
        return None
    return {
        "level": int(level_match.group(1)),
        "problem_id": int(problem_match.group(1)),
    }
