"""LLM-first source/tool action planner for V2 chat turns."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from autoad_researcher.assistant.llm_runtime import runtime_trace_fields
from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace
from autoad_researcher.source_normalizer import extract_first_source_candidate, extract_first_url, is_repository_url, normalize_repository_reference
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SourceActionType = Literal[
    "answer_only",
    "register_webpage",
    "register_github_repo",
    "web_search",
    "github_discovery",
    "git_clone",
    "repo_summarize",
    "ask_clarification",
]


class ToolCapability(BaseModel):
    """One tool/action capability exposed to the source action planner."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["available", "unavailable"] = "available"
    description: str


class RepositoryHint(BaseModel):
    """Repository candidate supplied as planner context, not routing logic."""

    model_config = ConfigDict(extra="forbid")

    hint_id: str
    label: str
    url: str
    source: str
    scope: str | None = None


class SourceAction(BaseModel):
    """One structured source/tool action proposed by the planner."""

    model_config = ConfigDict(extra="forbid")

    action_type: SourceActionType
    target: str = ""
    source_url: str | None = None
    query: str | None = None
    repository_hint_id: str | None = None
    source_kind: Literal["webpage", "github_repo", "paper_pdf"] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    rationale: str = ""

    @field_validator("source_url")
    @classmethod
    def _clean_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = extract_first_url(value) or value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _validate_action_payload(self):
        if self.action_type == "register_webpage" and not self.source_url:
            raise ValueError("source_url is required for register_webpage")
        if self.action_type in {"register_github_repo", "git_clone"} and not (self.source_url or self.repository_hint_id):
            raise ValueError(f"source_url or repository_hint_id is required for {self.action_type}")
        if self.action_type == "web_search" and not self.query:
            raise ValueError("query is required for web_search")
        if self.action_type == "github_discovery" and not (self.query or self.target):
            raise ValueError("query or target is required for github_discovery")
        return self


class SourceActionPlan(BaseModel):
    """Planner result for source/tool actions."""

    model_config = ConfigDict(extra="forbid")

    actions: list[SourceAction] = Field(default_factory=list)
    user_visible_summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


def plan_source_actions(
    *,
    run_dir: Path,
    user_input: str,
    attachments: list[str] | None = None,
    transcript_tail: list[dict[str, Any]] | None = None,
    existing_contract_draft: dict[str, Any] | None = None,
    source_registry: list[dict[str, Any]] | None = None,
    pending_jobs: list[dict[str, Any]] | None = None,
    api_key: str = "",
    provider_url: str = "",
    model: str = "deepseek-v4-flash",
) -> SourceActionPlan:
    """Plan source/tool actions.

    Deterministic code handles only structured signals such as uploads and
    explicit URLs. Natural-language requests require an LLM plan; without one
    they remain ordinary chat.
    """

    explicit = plan_explicit_source_actions(
        user_input=user_input,
        attachments=attachments,
        source_registry=source_registry,
    )
    if explicit is not None:
        return explicit

    if not api_key:
        return SourceActionPlan(
            actions=[],
            confidence=0.0,
            reason="No LLM source action planner is available for natural-language tool intent.",
        )

    tool_capabilities = default_tool_capabilities()
    repository_hints = load_repository_hints(run_dir)
    messages = _build_source_action_messages(
        user_input=user_input,
        transcript_tail=transcript_tail,
        existing_contract_draft=existing_contract_draft,
        source_registry=source_registry,
        pending_jobs=pending_jobs,
        tool_capabilities=tool_capabilities,
        repository_hints=repository_hints,
    )

    selector = PromptSelector()
    profile = selector.profile_for_v2_component("source_action_planner")
    system_prompt = messages[0]["content"] if messages else ""

    from autoad_researcher.ui.chat_client import call_research_chat

    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=8,
        priority="routing",
        response_format_json=True,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    reply_text = str(result.get("reply") or "")
    payload = _parse_json_object(reply_text)
    if result.get("error") or payload is None:
        append_llm_trace(
            run_dir,
            call_site="source_action_planner",
            prompt_id=profile.prompt_id,
            prompt_version=profile.prompt_version,
            prompt_text=system_prompt,
            model=model,
            provider_url=provider_url,
            messages=messages,
            raw_output=reply_text,
            parse_status="error",
            schema_validation="skipped",
            fallback_reason="llm_error_or_non_json",
            latency_ms=latency_ms,
            **runtime_trace_fields(result),
        )
        return SourceActionPlan(
            actions=[],
            confidence=0.0,
            reason="LLM source action planner failed or returned non-JSON output.",
        )
    try:
        plan = SourceActionPlan.model_validate(payload)
    except Exception as exc:
        append_llm_trace(
            run_dir,
            call_site="source_action_planner",
            prompt_id=profile.prompt_id,
            prompt_version=profile.prompt_version,
            prompt_text=system_prompt,
            model=model,
            provider_url=provider_url,
            messages=messages,
            raw_output=reply_text,
            parse_status="ok",
            schema_validation="error",
            fallback_reason="schema_validation_error",
            latency_ms=latency_ms,
            **runtime_trace_fields(result),
        )
        return SourceActionPlan(
            actions=[],
            confidence=0.0,
            reason=f"LLM source action planner output failed schema validation: {exc}",
        )
    append_llm_trace(
        run_dir,
        call_site="source_action_planner",
        prompt_id=profile.prompt_id,
        prompt_version=profile.prompt_version,
        prompt_text=system_prompt,
        model=model,
        provider_url=provider_url,
        messages=messages,
        raw_output=reply_text,
        parse_status="ok",
        schema_validation="ok",
        latency_ms=latency_ms,
        **runtime_trace_fields(result),
    )
    return validate_source_action_plan(plan, repository_hints=repository_hints)


def validate_source_action_plan(
    plan: SourceActionPlan,
    *,
    repository_hints: list[RepositoryHint] | None = None,
) -> SourceActionPlan:
    """Resolve hint IDs and keep only executable, schema-valid actions."""

    hints = {hint.hint_id: hint for hint in repository_hints or []}
    actions: list[SourceAction] = []
    for action in plan.actions:
        updated = action
        if action.repository_hint_id:
            hint = hints.get(action.repository_hint_id)
            if hint is None:
                continue
            if action.source_url is None:
                updated = action.model_copy(update={"source_url": hint.url, "target": action.target or hint.label})
        if updated.action_type in {"register_github_repo", "git_clone"} and updated.source_url:
            candidate = normalize_repository_reference(updated.source_url)
            if candidate is None:
                continue
            updated = updated.model_copy(update={"source_url": candidate.normalized_ref, "source_kind": "github_repo"})
        if updated.action_type == "register_webpage" and updated.source_url:
            if is_repository_url(updated.source_url):
                updated = updated.model_copy(update={"action_type": "register_github_repo", "source_kind": "github_repo"})
        actions.append(updated)
    return plan.model_copy(update={"actions": actions})


def default_tool_capabilities() -> list[ToolCapability]:
    """Planner-facing source/tool capabilities."""

    return [
        ToolCapability(
            name="register_webpage",
            description="Register an explicit non-GitHub URL as a source, then queue fetch/markdown parsing.",
        ),
        ToolCapability(
            name="register_github_repo",
            description="Register an explicit GitHub repository URL as a source.",
        ),
        ToolCapability(
            name="web_search",
            description="Search the web for candidate sources. Results are candidate_source_only until fetched and parsed.",
        ),
        ToolCapability(
            name="github_discovery",
            description="Discover a GitHub repository when the user gives a project/method name rather than an exact URL.",
        ),
        ToolCapability(
            name="git_clone",
            description="Clone an exact GitHub repository URL through controlled acquisition and attestation.",
        ),
        ToolCapability(
            name="repo_summarize",
            description="Summarize an acquired repository for Evidence after git_clone succeeds.",
        ),
    ]


def load_repository_hints(run_dir: Path) -> list[RepositoryHint]:
    """Load planner repository hints from configured, auditable project context."""

    hints: list[RepositoryHint] = []
    config_path = _find_project_root(run_dir) / "configs" / "benchmarks" / "internal_patchcore_mvtec_bottle_v1.yaml"
    if not config_path.is_file():
        return hints

    text = config_path.read_text(encoding="utf-8")
    url_match = re.search(r"(?m)^\s*url:\s*(https?://\S+)\s*$", text)
    baseline_match = re.search(r"(?m)^\s*baseline_name:\s*(.+?)\s*$", text)
    implementation_match = re.search(r"(?m)^\s*implementation_name:\s*(.+?)\s*$", text)
    scope_match = re.search(r"(?m)^\s*scope:\s*(.+?)\s*$", text)
    if url_match and baseline_match:
        label = baseline_match.group(1).strip()
        implementation = implementation_match.group(1).strip() if implementation_match else label
        hints.append(
            RepositoryHint(
                hint_id="internal_benchmark_patchcore",
                label=f"{label} ({implementation})",
                url=url_match.group(1).strip(),
                source=str(config_path.relative_to(_find_project_root(run_dir))),
                scope=scope_match.group(1).strip() if scope_match else None,
            )
        )
    return hints


def _build_source_action_messages(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    source_registry: list[dict[str, Any]] | None,
    pending_jobs: list[dict[str, Any]] | None,
    tool_capabilities: list[ToolCapability],
    repository_hints: list[RepositoryHint],
) -> list[dict[str, str]]:
    system = PromptSelector().build_system_prompt_for_v2_component("source_action_planner")
    context = {
        "transcript_tail": transcript_tail or [],
        "existing_contract_draft": existing_contract_draft or {},
        "source_registry": source_registry or [],
        "pending_jobs": pending_jobs or [],
        "tool_capabilities": [item.model_dump(mode="json") for item in tool_capabilities],
        "repository_hints": [item.model_dump(mode="json") for item in repository_hints],
    }
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": "Context JSON:\n" + _json_text(context)},
        {"role": "user", "content": user_input},
    ]


def plan_explicit_source_actions(
    *,
    user_input: str,
    attachments: list[str] | None,
    source_registry: list[dict[str, Any]] | None = None,
) -> SourceActionPlan | None:
    if attachments:
        return SourceActionPlan(
            actions=[
                SourceAction(
                    action_type="answer_only",
                    target="uploaded attachment",
                    source_kind="paper_pdf",
                    confidence=1.0,
                    rationale="Attachment upload is a structured source signal handled by the upload route.",
                )
            ],
            confidence=1.0,
            reason="Structured upload signal.",
        )

    candidate = extract_first_source_candidate(user_input.strip())
    if candidate is None:
        return None
    url = candidate.normalized_ref
    explicit_repo = candidate.source_kind == "github_repo"
    action_type: SourceActionType = "register_github_repo" if explicit_repo else "register_webpage"
    if explicit_repo:
        repo_candidate = normalize_repository_reference(url)
        if repo_candidate is not None:
            url = repo_candidate.normalized_ref
    source_kind: Literal["webpage", "github_repo", "paper_pdf"] = "github_repo" if action_type == "register_github_repo" else "webpage"
    return SourceActionPlan(
        actions=[
            SourceAction(
                action_type=action_type,
                target=url,
                source_url=url,
                source_kind=source_kind,
                confidence=1.0,
                rationale="Explicit URL supplied by user.",
            )
        ],
        confidence=1.0,
        reason="Structured URL signal.",
    )


def explicit_source_input_is_url_only(user_input: str) -> bool:
    """Return True only when the complete input is one syntactically valid URL."""

    text = user_input.strip().strip("<>()[]{}\"'")
    candidate = extract_first_source_candidate(text)
    if candidate is None:
        return False
    return text == candidate.raw_ref


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "{}"


def _find_project_root(run_dir: Path) -> Path:
    current = run_dir.resolve()
    for path in [current, *current.parents]:
        if (path / "pyproject.toml").is_file() and (path / "src" / "autoad_researcher").is_dir():
            return path
    return Path(__file__).resolve().parents[4]
