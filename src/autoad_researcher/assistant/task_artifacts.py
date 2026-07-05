"""Task draft and confirmation artifacts for AutoAD Assistant Round 6."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1
from autoad_researcher.assistant.events import AssistantEvent
from autoad_researcher.assistant.probe import WhatWeKnow
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.assistant.session_store import SessionStore
from autoad_researcher.core.run_id import run_dir_path


CHAT_TRANSCRIPT_ARTIFACT = Path("conversation/chat_transcript.jsonl")
WHAT_WE_KNOW_ARTIFACT = Path("conversation/what_we_know.json")
ASSISTANT_UNDERSTANDING_ARTIFACT = Path("conversation/assistant_understanding.jsonl")
USER_CORRECTIONS_ARTIFACT = Path("conversation/user_corrections.jsonl")
TASK_DRAFT_JSON_ARTIFACT = Path("task/research_task_draft.json")
TASK_DRAFT_MD_ARTIFACT = Path("task/research_task_draft.md")
TASK_CONFIRMED_JSON_ARTIFACT = Path("task/research_task_confirmed.json")


class AssistantUnderstandingRecord(BaseModel):
    """One structured assistant understanding entry."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    summary: str = Field(min_length=1)
    missing_fields: list[str] = Field(default_factory=list)
    evidence_artifacts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AssistantTaskArtifactService:
    """Write intent-alignment artifacts without executing pipeline work."""

    def __init__(self, runs_root: str | Path = "runs", *, store: SessionStore | None = None) -> None:
        self._runs_root = Path(runs_root)
        self._store = store or SessionStore(runs_root=runs_root)

    def write_what_we_know(self, what_we_know: WhatWeKnow) -> Path:
        return self._write_json(what_we_know.run_id, WHAT_WE_KNOW_ARTIFACT, what_we_know.model_dump(mode="json"))

    def append_user_correction(self, run_id: str, event: AssistantEvent) -> Path:
        payload = {
            "schema_version": 1,
            "event": event.model_dump(mode="json"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return self._append_jsonl(run_id, USER_CORRECTIONS_ARTIFACT, payload)

    def append_assistant_understanding(self, record: AssistantUnderstandingRecord) -> Path:
        return self._append_jsonl(
            record.run_id,
            ASSISTANT_UNDERSTANDING_ARTIFACT,
            record.model_dump(mode="json"),
        )

    def create_research_task_draft(
        self,
        *,
        session: AutoADAssistantSession,
        what_we_know: WhatWeKnow,
        metric_command: str,
        metric_name: str,
        metric_direction: Literal["maximize", "minimize"],
        baseline: str | None = None,
        baseline_value: float | None = None,
        ambition: Literal["push_max", "reach_target", "beat_baseline"] = "beat_baseline",
        ambition_target: float | None = None,
        scope: Literal["novelty_leaning", "effect_leaning", "mixed"] = "mixed",
        constraints: list[str] | None = None,
        dataset: str | None = None,
        compute_budget: str | None = None,
        user_idea: str | None = None,
        blocking_gaps: list[str] | None = None,
    ) -> tuple[ResearchTaskDraftV1, AutoADAssistantSession]:
        baseline_value_text = baseline or what_we_know.baseline_method
        if baseline_value_text is None:
            raise ValueError("baseline must be provided or available from WhatWeKnow")
        draft = ResearchTaskDraftV1(
            run_id=session.run_id,
            draft_id=_draft_id(session.run_id, metric_name, baseline_value_text),
            metric_command=metric_command,
            metric_name=metric_name,
            metric_direction=metric_direction,
            baseline=baseline_value_text,
            baseline_value=baseline_value,
            ambition=ambition,
            ambition_target=ambition_target,
            scope=scope,
            constraints=constraints or [],
            dataset=dataset or what_we_know.dataset,
            compute_budget=compute_budget,
            user_idea=user_idea,
            evidence_ids=what_we_know.evidence_artifacts,
            confirmation="draft",
        )
        self.write_what_we_know(what_we_know)
        self._write_json(session.run_id, TASK_DRAFT_JSON_ARTIFACT, draft.model_dump(mode="json", exclude_none=True))
        self._write_text(session.run_id, TASK_DRAFT_MD_ARTIFACT, _draft_markdown(draft))
        updated = session.model_copy(deep=True)
        updated.mode = "task_confirmation"
        updated.task.draft_ref = TASK_DRAFT_JSON_ARTIFACT.as_posix()
        updated.task.has_blocking_gaps = bool(blocking_gaps)
        updated.task.ready_for_pipeline = False
        updated.task.execution_approved = False
        self._store.save_session(updated)
        return draft, updated

    def confirm_research_task(
        self,
        *,
        session: AutoADAssistantSession,
        draft: ResearchTaskDraftV1,
        confirmation_evidence_id: str,
        confirmed_at: datetime | None = None,
    ) -> tuple[ResearchTaskDraftV1, AutoADAssistantSession]:
        confirmation_evidence_id = confirmation_evidence_id.strip()
        if not confirmation_evidence_id:
            raise ValueError("confirmation_evidence_id must not be empty")
        if draft.run_id != session.run_id:
            raise ValueError("draft run_id must match session run_id")
        if session.task.has_blocking_gaps:
            raise ValueError("cannot confirm research task while blocking gaps remain")
        if draft.confirmation == "confirmed":
            confirmed = draft
        else:
            confirmed = draft.model_copy(
                update={
                    "confirmation": "confirmed",
                    "confirmed_by_user_at": confirmed_at or datetime.now(timezone.utc),
                    "confirmation_evidence_id": confirmation_evidence_id,
                }
            )
        self._write_json(session.run_id, TASK_CONFIRMED_JSON_ARTIFACT, confirmed.model_dump(mode="json", exclude_none=True))
        updated = session.model_copy(deep=True)
        updated.mode = "pipeline_ready"
        updated.task.draft_ref = TASK_DRAFT_JSON_ARTIFACT.as_posix()
        updated.task.confirmed_ref = TASK_CONFIRMED_JSON_ARTIFACT.as_posix()
        updated.task.has_blocking_gaps = False
        updated.task.ready_for_pipeline = True
        updated.task.execution_approved = False
        self._store.save_session(updated)
        return confirmed, updated

    def _run_dir(self, run_id: str) -> Path:
        return run_dir_path(self._runs_root, run_id)

    def _path(self, run_id: str, artifact: Path) -> Path:
        if artifact.is_absolute() or ".." in artifact.parts:
            raise ValueError(f"unsafe assistant artifact path: {artifact}")
        path = self._run_dir(run_id) / artifact
        resolved = path.resolve()
        resolved.relative_to(self._run_dir(run_id).resolve())
        return path

    def _write_json(self, run_id: str, artifact: Path, payload: dict[str, object]) -> Path:
        path = self._path(run_id, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_text(self, run_id: str, artifact: Path, text: str) -> Path:
        path = self._path(run_id, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _append_jsonl(self, run_id: str, artifact: Path, payload: dict[str, object]) -> Path:
        path = self._path(run_id, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
        return path


def _draft_id(run_id: str, metric_name: str, baseline: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{metric_name}:{baseline}".encode("utf-8")).hexdigest()[:12]
    return f"draft_{digest}"


def _draft_markdown(draft: ResearchTaskDraftV1) -> str:
    lines = [
        "# AutoAD Research Task Draft",
        "",
        f"- Metric: `{draft.metric_name}` ({draft.metric_direction})",
        f"- Metric command: `{draft.metric_command}`",
        f"- Baseline: `{draft.baseline}`",
        f"- Ambition: `{draft.ambition}`",
        f"- Scope: `{draft.scope}`",
        f"- Dataset: `{draft.dataset or 'unknown'}`",
        "- Constraints:",
    ]
    if draft.constraints:
        lines.extend(f"  - {item}" for item in draft.constraints)
    else:
        lines.append("  - none")
    lines.extend([
        "",
        "This draft defines the research goal and evaluation constraints only.",
        "It does not choose methods, algorithms, hyperparameters, patch hooks, or implementation variants.",
    ])
    return "\n".join(lines) + "\n"
