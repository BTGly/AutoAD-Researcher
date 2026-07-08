"""Research Intent Draft artifacts for the local research assistant.

These files are UI audit material. They do not participate in the Stage 3
artifact chain and must not trigger pipeline execution.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.core.run_id import validate_run_id
from autoad_researcher.schemas.approvals import IntentConfirmation as UIIntentConfirmation, Stage3Approval
from autoad_researcher.schemas.intake import InputTask
from autoad_researcher.ui.chat_transcript import redact_secrets

INTENT_DRAFT_DIR = "ui_chat"
INTENT_DRAFT_JSON = "intent_draft.json"
INTENT_DRAFT_MD = "intent_draft.md"
CLARIFICATION_INPUT_JSON = "clarification_input.json"
APPROVALS_DIR = "approvals"
INTENT_CONFIRMATION_JSON = "intent_confirmation.json"

ProblemType = Literal[
    "accuracy_improvement",
    "resource_efficiency",
    "robustness",
    "ablation",
    "other",
]
IntentDecision = Literal["approved", "rejected", "needs_revision"]
PatchScopeStatus = Literal["defer_to_patch_planner_after_repo_inspection"]


class ResearchIntentDraft(BaseModel):
    """Structured research intent draft produced from UI chat."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    source: Literal["ui_chat"] = "ui_chat"
    research_goal: str = Field(min_length=1)
    problem_type: ProblemType = "other"
    primary_metrics: list[str] = Field(default_factory=list)
    guardrail_metrics: list[str] = Field(default_factory=list)
    allowed_change_scope: list[str] = Field(default_factory=list)
    forbidden_change_scope: list[str] = Field(default_factory=list)
    patch_scope_status: PatchScopeStatus = "defer_to_patch_planner_after_repo_inspection"
    benchmark_scope: dict[str, Any] = Field(default_factory=dict)
    success_criteria: str = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def validate_no_secret_like_content(self) -> "ResearchIntentDraft":
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
        if redact_secrets(payload) != payload:
            raise ValueError("intent draft must not contain API-key-like secrets")
        return self


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from an LLM response."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty intent draft response")
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("intent draft response did not contain a JSON object")
        stripped = stripped[start:end + 1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid intent draft JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("intent draft JSON must be an object")
    return data


def parse_intent_draft_response(text: str, *, run_id: str) -> ResearchIntentDraft:
    """Parse and validate a strict JSON LLM response as ``ResearchIntentDraft``."""
    validate_run_id("runs", run_id)
    data = extract_json_object(text)
    data["run_id"] = run_id
    data.setdefault("source", "ui_chat")
    data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    return ResearchIntentDraft.model_validate(data)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def intent_draft_prompt_payload(*, run_id: str, transcript_tail: list[dict], context: dict[str, Any] | None) -> list[dict[str, str]]:
    """Build messages that ask the LLM for strict JSON only."""
    schema_hint = {
        "research_goal": "one concise sentence",
        "problem_type": "accuracy_improvement | resource_efficiency | robustness | ablation | other",
        "primary_metrics": ["metric_name"],
        "guardrail_metrics": ["metric_name"],
        "allowed_change_scope": ["functional research-level constraint only; no file paths"],
        "forbidden_change_scope": ["functional research-level constraint only; no file paths"],
        "patch_scope_status": "defer_to_patch_planner_after_repo_inspection",
        "benchmark_scope": {"dataset": "", "category": "", "baseline": ""},
        "success_criteria": "explicit acceptance rule",
        "risks": ["risk"],
        "open_questions": ["question"],
    }
    user_payload = {
        "run_id": run_id,
        "recent_transcript": _redact_value(transcript_tail[-8:]),
        "run_context": _redact_value(context or {}),
        "required_json_shape": schema_hint,
    }
    return [
        {
            "role": "system",
            "content": (
                "You are AutoAD-Researcher's advisory research intent drafter. "
                "Return exactly one JSON object and no markdown. Do not include API keys, "
                "headers, tool outputs, or raw logs. Do not claim code was modified or a "
                "pipeline was executed. Keep allowed_change_scope and forbidden_change_scope "
                "at the research/function level; do not output file paths, module paths, or "
                "patch hooks. Set patch_scope_status to defer_to_patch_planner_after_repo_inspection."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, default=str),
        },
    ]


def save_intent_draft(run_dir: Path, draft: ResearchIntentDraft) -> Path:
    """Save ``intent_draft.json`` and a compact markdown rendering."""
    validate_run_id(run_dir.parent, run_dir.name)
    target_dir = run_dir / INTENT_DRAFT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / INTENT_DRAFT_JSON
    json_path.write_text(
        json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path = target_dir / INTENT_DRAFT_MD
    md_path.write_text(intent_draft_markdown(draft), encoding="utf-8")
    return json_path


def load_intent_draft(run_dir: Path) -> ResearchIntentDraft | None:
    path = run_dir / INTENT_DRAFT_DIR / INTENT_DRAFT_JSON
    if not path.is_file():
        return None
    return ResearchIntentDraft.model_validate_json(path.read_text(encoding="utf-8"))


def intent_draft_markdown(draft: ResearchIntentDraft) -> str:
    """Render a human-readable intent draft summary."""
    def bullet(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- none"

    return (
        "# Research Intent Draft\n\n"
        f"- run_id: `{draft.run_id}`\n"
        f"- problem_type: `{draft.problem_type}`\n"
        f"- created_at: `{draft.created_at}`\n\n"
        "## Research Goal\n\n"
        f"{draft.research_goal}\n\n"
        "## Primary Metrics\n\n"
        f"{bullet(draft.primary_metrics)}\n\n"
        "## Guardrail Metrics\n\n"
        f"{bullet(draft.guardrail_metrics)}\n\n"
        "## Allowed Change Scope\n\n"
        f"{bullet(draft.allowed_change_scope)}\n\n"
        "## Forbidden Change Scope\n\n"
        f"{bullet(draft.forbidden_change_scope)}\n\n"
        "## Patch Scope Status\n\n"
        f"- {draft.patch_scope_status}\n\n"
        "## Success Criteria\n\n"
        f"{draft.success_criteria}\n\n"
        "## Risks\n\n"
        f"{bullet(draft.risks)}\n\n"
        "## Open Questions\n\n"
        f"{bullet(draft.open_questions)}\n"
    )


def intent_draft_to_clarification_input(draft: ResearchIntentDraft) -> dict[str, Any]:
    """Map a UI intent draft into existing clarification/intake vocabulary."""
    benchmark = draft.benchmark_scope
    dataset = benchmark.get("dataset") if isinstance(benchmark, dict) else None
    baseline = benchmark.get("baseline") if isinstance(benchmark, dict) else None
    constraints = [
        *draft.allowed_change_scope,
        *[f"forbidden: {item}" for item in draft.forbidden_change_scope],
        f"patch_scope_status: {draft.patch_scope_status}",
        *draft.risks,
    ]
    task = InputTask(
        run_id=draft.run_id,
        request=draft.research_goal,
        user_idea=draft.research_goal,
        baseline=baseline if isinstance(baseline, str) and baseline.strip() else None,
        dataset=dataset if isinstance(dataset, str) and dataset.strip() else None,
        constraints=constraints,
    )
    return {
        "schema_version": 1,
        "source": "ui_intent_draft",
        "draft_ref": f"{INTENT_DRAFT_DIR}/{INTENT_DRAFT_JSON}",
        "input_task": task.model_dump(mode="json"),
        "clarification_hints": {
            "problem_type": draft.problem_type,
            "primary_metrics": draft.primary_metrics,
            "guardrail_metrics": draft.guardrail_metrics,
            "success_criteria": draft.success_criteria,
            "patch_scope_status": draft.patch_scope_status,
            "open_questions": draft.open_questions,
        },
    }


def save_clarification_input(run_dir: Path, draft: ResearchIntentDraft) -> Path:
    validate_run_id(run_dir.parent, run_dir.name)
    target_dir = run_dir / INTENT_DRAFT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / CLARIFICATION_INPUT_JSON
    path.write_text(
        json.dumps(intent_draft_to_clarification_input(draft), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def save_intent_confirmation(
    run_dir: Path,
    *,
    decision: IntentDecision,
    comment: str | None = None,
    reviewer: str = "local_user",
) -> Path:
    """Save a human confirmation checkpoint for the current intent draft."""
    validate_run_id(run_dir.parent, run_dir.name)
    draft_path = run_dir / INTENT_DRAFT_DIR / INTENT_DRAFT_JSON
    if not draft_path.is_file():
        raise ValueError("intent_draft.json is required before confirmation")
    confirmation = UIIntentConfirmation(
        run_id=run_dir.name,
        decision=decision,
        reviewer=reviewer,
        comment=comment,
    )
    target_dir = run_dir / APPROVALS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / INTENT_CONFIRMATION_JSON
    path.write_text(
        json.dumps(confirmation.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_intent_confirmation(run_dir: Path) -> UIIntentConfirmation | None:
    path = run_dir / APPROVALS_DIR / INTENT_CONFIRMATION_JSON
    if not path.is_file():
        return None
    return UIIntentConfirmation.model_validate_json(path.read_text(encoding="utf-8"))


def save_stage3_approval(
    run_dir: Path,
    *,
    decision_type: Literal["patch_approval", "run_approval"],
    confirmed_by_user: bool,
    user_confirmation_text: str | None = None,
) -> Path:
    """Save a Stage3Approval artifact from the UI without executing pipeline actions."""
    validate_run_id(run_dir.parent, run_dir.name)
    approval = Stage3Approval(
        run_id=run_dir.name,
        decision_type=decision_type,
        confirmed_by_user=confirmed_by_user,
        user_confirmation_text=user_confirmation_text,
        created_at=datetime.now(timezone.utc),
        evidence_kind="approval_artifact",
    )
    target_dir = run_dir / APPROVALS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = "patch_approval.json" if decision_type == "patch_approval" else "run_approval.json"
    path = target_dir / filename
    path.write_text(
        json.dumps(approval.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_stage3_approval(
    run_dir: Path,
    *,
    decision_type: Literal["patch_approval", "run_approval"],
) -> Stage3Approval | None:
    filename = "patch_approval.json" if decision_type == "patch_approval" else "run_approval.json"
    path = run_dir / APPROVALS_DIR / filename
    if not path.is_file():
        return None
    return Stage3Approval.model_validate_json(path.read_text(encoding="utf-8"))
