"""Requirement discovery for V2 research conversations.

The resolver decides which facts block the next stage. Rules in this module
canonicalize values and validate readiness; they do not force a single metric
or require optional method-design hints.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from autoad_researcher.assistant.prompt_selector import PromptSelector
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace
from pydantic import BaseModel, ConfigDict, Field


NeedCategory = Literal[
    "intent",
    "material",
    "experiment_object",
    "evaluation",
    "environment",
    "safety",
    "execution",
]
RequiredFor = Literal[
    "chat",
    "plan",
    "repo_analysis",
    "experiment_design",
    "patch",
    "run",
    "final_report",
]
Necessity = Literal["required_now", "required_later", "optional", "auto_fillable"]
NeedSource = Literal["user_confirmed", "user", "llm_inferred", "artifact", "repo", "paper", "default", "unknown"]
TaskProfile = Literal[
    "empirical_model_research",
    "systems_optimization",
    "code_diagnosis",
    "general_research",
]
StageGoal = Literal[
    "clarify_intent",
    "generate_plan",
    "analyze_repo",
    "design_experiment",
    "patch_code",
    "run_experiment",
    "review_result",
]


class RequirementNeed(BaseModel):
    """A single fact/material/resource needed for a stage."""

    model_config = ConfigDict(extra="forbid")

    name: str
    category: NeedCategory
    required_for: RequiredFor
    necessity: Necessity
    current_value: Any | None = None
    source: NeedSource = "unknown"
    confidence: float = 0.0
    blocking: bool = False
    question_to_user: str | None = None
    evidence_quote: str | None = None


class RequiredNeedSpec(BaseModel):
    """Validated requirement state for the current conversation stage."""

    model_config = ConfigDict(extra="forbid")

    task_summary: str = ""
    inferred_task_type: str = "general_research"
    task_profile: TaskProfile = "general_research"
    task_profile_source: NeedSource = "unknown"
    task_profile_evidence: str | None = None
    current_stage_goal: StageGoal = "generate_plan"
    needs: list[RequirementNeed] = Field(default_factory=list)
    blocking_needs: list[str] = Field(default_factory=list)
    next_best_question: str | None = None
    ready_for_plan: bool = False
    ready_for_repo_analysis: bool = False
    ready_for_experiment_design: bool = False
    ready_for_patch: bool = False
    ready_for_run: bool = False


def discover_required_needs(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None = None,
    existing_contract_draft: dict[str, Any] | None = None,
    source_registry: list[dict[str, Any]] | None = None,
    usable_evidence: list[dict[str, Any]] | None = None,
    created_jobs: list[dict[str, Any]] | None = None,
    current_stage_goal: StageGoal = "generate_plan",
    answerability: dict[str, Any] | None = None,
    run_artifacts_summary: dict[str, Any] | None = None,
    llm_payload: dict[str, Any] | None = None,
    task_profile_proposal: TaskProfile | None = None,
    task_profile_evidence: str | None = None,
) -> RequiredNeedSpec:
    """Discover and validate requirements for the current stage.

    `llm_payload` can carry an LLM-produced RequiredNeedSpec JSON. When absent,
    the resolver uses a conservative schema-driven fallback so tests and local
    development do not depend on a live model.
    """

    if llm_payload is not None:
        spec = RequiredNeedSpec.model_validate(llm_payload)
        _apply_execution_authorization_evidence(
            spec,
            current_user_text=user_input,
            context_text=_combined_user_text(user_input, transcript_tail),
            existing_execution_mode=(existing_contract_draft or {}).get("execution_mode"),
        )
        spec = canonicalize_need_values(spec)
        _recover_directional_plan_success_criteria(spec, _combined_user_text(user_input, transcript_tail))
        return validate_need_spec(spec, user_text=_combined_user_text(user_input, transcript_tail))

    text = _combined_user_text(user_input, transcript_tail)
    draft = existing_contract_draft or {}
    sources = source_registry or []
    evidence = usable_evidence or []
    values = _autofill_values(
        text,
        draft,
        sources,
        evidence,
        run_artifacts_summary or {},
        current_user_text=user_input,
    )
    proposal_evidence = (task_profile_evidence or "").strip()
    proposal_is_supported = bool(
        task_profile_proposal
        and task_profile_proposal != "general_research"
        and proposal_evidence
        and proposal_evidence in text
    )
    task_type = (
        _task_type_for_profile(task_profile_proposal)
        if proposal_is_supported
        else _infer_task_type(text, values)
    )
    profile = task_profile_proposal if proposal_is_supported else _task_profile_for_type(task_type)
    profile_evidence = proposal_evidence if proposal_is_supported else _evidence_excerpt(text)
    needs = _build_stage_needs(task_type, current_stage_goal, values, user_text=text)
    spec = RequiredNeedSpec(
        task_summary=_task_summary(task_type, values),
        inferred_task_type=task_type,
        task_profile=profile,
        task_profile_source="llm_inferred",
        task_profile_evidence=profile_evidence,
        current_stage_goal=current_stage_goal,
        needs=needs,
    )
    spec = canonicalize_need_values(spec)
    _recover_directional_plan_success_criteria(spec, text)
    return validate_need_spec(spec, user_text=text)


def discover_required_needs_with_llm(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None = None,
    existing_contract_draft: dict[str, Any] | None = None,
    source_registry: list[dict[str, Any]] | None = None,
    usable_evidence: list[dict[str, Any]] | None = None,
    created_jobs: list[dict[str, Any]] | None = None,
    current_stage_goal: StageGoal = "generate_plan",
    answerability: dict[str, Any] | None = None,
    run_artifacts_summary: dict[str, Any] | None = None,
    api_key: str = "",
    provider_url: str = "",
    model: str = "deepseek-v4-flash",
    run_dir: Path | None = None,
    task_profile_proposal: TaskProfile | None = None,
    task_profile_evidence: str | None = None,
    requires_llm_enrichment: bool = False,
) -> RequiredNeedSpec:
    """Deterministic-first requirement discovery with bounded LLM enrichment.

    The LLM proposes the task type, required needs, and next question. Core code
    validates the schema, canonicalizes enum-like values, and applies safety
    rules. Fallback is used only when no model config is available or the model
    output is invalid.
    """

    fallback_kwargs = {
        "user_input": user_input,
        "transcript_tail": transcript_tail,
        "existing_contract_draft": existing_contract_draft,
        "source_registry": source_registry,
        "usable_evidence": usable_evidence,
        "created_jobs": created_jobs,
        "current_stage_goal": current_stage_goal,
        "answerability": answerability,
        "run_artifacts_summary": run_artifacts_summary,
        "task_profile_proposal": task_profile_proposal,
        "task_profile_evidence": task_profile_evidence,
    }
    fallback_spec = discover_required_needs(**fallback_kwargs)
    if not api_key or not _should_enrich_need_spec(fallback_spec, requires_llm_enrichment):
        return fallback_spec

    cache_key = _need_discovery_cache_key(
        user_input=user_input,
        transcript_tail=transcript_tail,
        existing_contract_draft=existing_contract_draft,
        source_registry=source_registry,
        usable_evidence=usable_evidence,
        created_jobs=created_jobs,
        current_stage_goal=current_stage_goal,
        answerability=answerability,
        run_artifacts_summary=run_artifacts_summary,
        task_profile_proposal=task_profile_proposal,
        task_profile_evidence=task_profile_evidence,
    )
    cached = _load_need_discovery_cache(run_dir, cache_key)
    if cached is not None:
        return validate_need_spec(cached, user_text=_combined_user_text(user_input, transcript_tail))

    messages = _build_need_discovery_messages(
        user_input=user_input,
        transcript_tail=transcript_tail,
        existing_contract_draft=existing_contract_draft,
        source_registry=source_registry,
        usable_evidence=usable_evidence,
        created_jobs=created_jobs,
        current_stage_goal=current_stage_goal,
        answerability=answerability,
        run_artifacts_summary=run_artifacts_summary,
        task_profile_proposal=task_profile_proposal,
        task_profile_evidence=task_profile_evidence,
    )
    selector = PromptSelector()
    profile = selector.profile_for_v2_component("need_discovery")
    system_prompt = messages[0]["content"] if messages else ""

    from autoad_researcher.ui.chat_client import call_research_chat

    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=8,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    reply_text = str(result.get("reply") or "")
    payload = _parse_json_object(reply_text)
    if result.get("error") or payload is None:
        append_llm_trace(
            run_dir,
            call_site="need_discovery",
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
        )
        return fallback_spec
    try:
        spec = RequiredNeedSpec.model_validate(payload)
    except Exception:
        append_llm_trace(
            run_dir,
            call_site="need_discovery",
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
        )
        return fallback_spec
    spec.current_stage_goal = current_stage_goal
    append_llm_trace(
        run_dir,
        call_site="need_discovery",
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
    )
    _apply_execution_authorization_evidence(
        spec,
        current_user_text=user_input,
        context_text=_combined_user_text(user_input, transcript_tail),
        existing_execution_mode=(existing_contract_draft or {}).get("execution_mode"),
    )
    spec = canonicalize_need_values(spec)
    _recover_directional_plan_success_criteria(spec, _combined_user_text(user_input, transcript_tail))
    spec = validate_need_spec(spec, user_text=_combined_user_text(user_input, transcript_tail))
    _save_need_discovery_cache(run_dir, cache_key, spec)
    return spec


def canonicalize_need_values(spec: RequiredNeedSpec) -> RequiredNeedSpec:
    """Canonicalize enum-like values while preserving source and confidence."""

    updated = spec.model_copy(deep=True)
    for need in updated.needs:
        if need.name == "metrics":
            need.current_value = canonicalize_metrics(need.current_value)
        elif need.name == "dataset" and isinstance(need.current_value, str):
            if re.search(r"mvtec\s*(ad)?", need.current_value, flags=re.IGNORECASE):
                need.current_value = "MVTec AD"
        elif need.name == "baseline" and isinstance(need.current_value, str):
            if re.search(r"(patch\s*)?core|pathcore", need.current_value, flags=re.IGNORECASE):
                need.current_value = "PatchCore"
        elif need.name == "execution_mode" and not need.current_value:
            need.current_value = "plan_only"
            need.source = "default"
            need.confidence = max(need.confidence, 0.8)
            need.evidence_quote = None
    return updated


def validate_need_spec(
    spec: RequiredNeedSpec,
    *,
    user_text: str | None = None,
) -> RequiredNeedSpec:
    """Recompute the task profile, blocking state, and readiness.

    Model-produced readiness flags are deliberately ignored. When user text is
    available, a need marked as user-sourced must carry a quote that occurs in
    that text; otherwise the unsupported value is discarded before readiness
    is calculated.
    """

    updated = spec.model_copy(deep=True)
    if user_text is not None:
        _validate_user_evidence(updated, user_text)
    _select_task_profile(updated, user_text or "")
    _ensure_profile_required_needs(updated)
    _ensure_stage_required_needs(updated)
    for need in updated.needs:
        if need.name in {"improvement_idea", "target_module"}:
            need.necessity = "optional"
            need.blocking = False
            continue
        if need.name in {"entrypoint", "config", "baseline_entrypoint", "baseline_config"}:
            need.necessity = "auto_fillable"
            need.blocking = False
            need.question_to_user = None
            continue
        if updated.current_stage_goal != "run_experiment" and (
            need.required_for == "run"
            or need.name in {"dataset_path", "python_env", "gpu", "cuda", "time_budget"}
        ):
            need.necessity = "required_later"
            need.blocking = False
            continue
        if need.necessity in {"optional", "required_later", "auto_fillable"}:
            need.blocking = False
            continue
        need.blocking = need.necessity == "required_now" and (
            _is_empty_need_value(need.current_value) or _is_template_only_need(need)
        )
        if not need.blocking and not _is_empty_need_value(need.current_value):
            need.question_to_user = None

    updated.blocking_needs = [need.name for need in updated.needs if need.blocking]
    updated.next_best_question = _next_best_question(updated.needs)
    updated.ready_for_plan = not any(
        need.blocking and need.required_for in {"chat", "plan"} for need in updated.needs
    )
    updated.ready_for_repo_analysis = _has_value(updated.needs, "repo") or _has_value(updated.needs, "baseline_repo")
    updated.ready_for_experiment_design = updated.ready_for_plan and _has_value(updated.needs, "metrics")
    updated.ready_for_patch = updated.ready_for_repo_analysis and _has_value(updated.needs, "allowed_change_scope")
    updated.ready_for_run = all(
        _has_value(updated.needs, name)
        for name in ("dataset_path", "python_env", "time_budget", "human_review_policy")
    )
    return updated


def _recover_directional_plan_success_criteria(spec: RequiredNeedSpec, user_text: str) -> None:
    if spec.current_stage_goal != "generate_plan":
        return
    user_supported_value = _success_criteria_from_text(user_text)
    if not user_supported_value:
        return
    for need in spec.needs:
        if need.name != "success_criteria":
            continue
        numeric_target = _numeric_improvement_criteria_from_text(user_text)
        if numeric_target or _is_empty_need_value(need.current_value):
            need.current_value = user_supported_value
            need.source = "user"
            need.confidence = max(need.confidence, 0.9)
            need.question_to_user = None
            need.evidence_quote = _evidence_excerpt(user_text)
        return


def _build_need_discovery_messages(
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    existing_contract_draft: dict[str, Any] | None,
    source_registry: list[dict[str, Any]] | None,
    usable_evidence: list[dict[str, Any]] | None,
    created_jobs: list[dict[str, Any]] | None,
    current_stage_goal: StageGoal,
    answerability: dict[str, Any] | None,
    run_artifacts_summary: dict[str, Any] | None,
    task_profile_proposal: TaskProfile | None = None,
    task_profile_evidence: str | None = None,
) -> list[dict[str, str]]:
    system = PromptSelector().build_system_prompt_for_v2_component("need_discovery")
    context = {
        "current_stage_goal": current_stage_goal,
        "transcript_tail": transcript_tail or [],
        "existing_contract_draft": existing_contract_draft or {},
        "source_registry": source_registry or [],
        "usable_evidence": usable_evidence or [],
        "created_jobs": created_jobs or [],
        "answerability": answerability or {},
        "run_artifacts_summary": run_artifacts_summary or {},
        "task_profile_proposal": task_profile_proposal,
        "task_profile_evidence": task_profile_evidence,
    }
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": "Context JSON:\n" + _json_text(context)},
        {"role": "user", "content": user_input},
    ]


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


def _should_enrich_need_spec(_spec: RequiredNeedSpec, requested: bool) -> bool:
    return requested


def _need_discovery_cache_key(**payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _need_discovery_cache_path(run_dir: Path, cache_key: str) -> Path:
    return run_dir / "assistant" / "need_discovery_cache" / f"{cache_key}.json"


def _load_need_discovery_cache(run_dir: Path | None, cache_key: str) -> RequiredNeedSpec | None:
    if run_dir is None:
        return None
    path = _need_discovery_cache_path(run_dir, cache_key)
    if not path.is_file():
        return None
    try:
        return RequiredNeedSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_need_discovery_cache(
    run_dir: Path | None,
    cache_key: str,
    spec: RequiredNeedSpec,
) -> None:
    if run_dir is None:
        return
    path = _need_discovery_cache_path(run_dir, cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    temp.replace(path)


def canonicalize_metrics(value: Any) -> list[str]:
    """Map metric phrases to canonical metric ids without deciding intent."""

    raw_items: list[str]
    if isinstance(value, list):
        raw_items = [str(item) for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        raw_items = [value]
    else:
        return []

    metrics: list[str] = []
    for item in raw_items:
        lowered = item.lower()
        item_metrics: list[str] = []
        if (
            re.search(r"(?<![A-Za-z0-9_])auroc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc-?roc(?![A-Za-z0-9_])", lowered)
            and any(token in item for token in ("两种", "两个", "主流"))
        ):
            item_metrics.extend(["image_level_auroc", "pixel_level_auroc"])
        if re.search(r"image[-_\s]*(level[-_\s]*)?(auc|auroc)|instance[-_\s]*(level[-_\s]*)?(auc|auroc)", lowered):
            item_metrics.append("image_level_auroc")
        if re.search(r"pixel[-_\s]*(level[-_\s]*)?(auc|auroc)|full[-_\s]*pixel[-_\s]*(auc|auroc)|定位", lowered):
            item_metrics.append("pixel_level_auroc")
        if re.search(r"\bpro\b|per[-_\s]*region[-_\s]*overlap|\bau[-_\s]*pro\b", lowered):
            item_metrics.append("pro")
        if re.search(r"\bf1\b|f1[-_\s]*score", lowered):
            item_metrics.append("f1")
        if re.search(r"accuracy|准确率", lowered):
            item_metrics.append("accuracy")
        if re.search(r"速度|推理速度|latency|throughput|fps", lowered):
            item_metrics.append("inference_latency")
        if re.search(r"显存|memory|vram", lowered):
            item_metrics.append("peak_vram")
        if not item_metrics and re.search(r"(?<![A-Za-z0-9_])auroc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc-?roc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc(?![A-Za-z0-9_])", lowered):
            item_metrics.append("image_level_auroc")
        if not item_metrics and item in {
            "image_level_auroc",
            "pixel_level_auroc",
            "pro",
            "f1",
            "accuracy",
            "inference_latency",
            "peak_vram",
        }:
            item_metrics.append(item)
        metrics.extend(item_metrics)
    return _unique(metrics)


def _combined_user_text(user_input: str, transcript_tail: list[dict[str, Any]] | None) -> str:
    parts = [
        str(entry.get("content", ""))
        for entry in (transcript_tail or [])
        if entry.get("role") == "user"
    ]
    parts.append(user_input)
    return "\n".join(part for part in parts if part.strip())


def _autofill_values(
    text: str,
    draft: dict[str, Any],
    source_registry: list[dict[str, Any]],
    usable_evidence: list[dict[str, Any]],
    run_artifacts_summary: dict[str, Any],
    *,
    current_user_text: str,
) -> dict[str, Any]:
    current_metrics = canonicalize_metrics(text)
    current_success_criteria = _success_criteria_from_text(text)
    execution_mode, execution_evidence, execution_conflict = _execution_mode_observation(current_user_text)
    use_existing_execution = (
        execution_mode is None
        and not execution_conflict
        and draft.get("execution_mode") in {
            "plan_only",
            "approve_each_step",
            "agent_assisted_after_approval",
        }
    )
    if execution_mode is None and not execution_conflict and not use_existing_execution:
        execution_mode, execution_evidence, execution_conflict = _execution_mode_observation(text)
    if execution_mode is not None:
        resolved_execution_mode = execution_mode
        execution_source: NeedSource = "user"
    elif use_existing_execution:
        resolved_execution_mode = draft.get("execution_mode")
        execution_source = "artifact"
    else:
        resolved_execution_mode = "plan_only"
        execution_source = "default"
    values: dict[str, Any] = {
        "research_goal": draft.get("research_goal") or _goal_from_text(text),
        "research_object": draft.get("research_object") or _research_object_from_text(text),
        "target_platform": draft.get("target_platform") or _target_platform_from_text(text),
        "workload": draft.get("workload") or _workload_from_text(text),
        "baseline": draft.get("baseline") or _baseline_from_text(text),
        "dataset": draft.get("dataset") or _dataset_from_text(text),
        "metrics": current_metrics or draft.get("primary_metrics") or draft.get("primary_metric"),
        "success_criteria": current_success_criteria or draft.get("success_criteria"),
        "execution_mode": resolved_execution_mode,
        "execution_mode_source": execution_source,
        "execution_mode_evidence": execution_evidence,
        "execution_mode_conflict": execution_conflict,
        "allowed_change_scope": draft.get("allowed_change_scope"),
        "forbidden_change_scope": draft.get("forbidden_change_scope"),
        "dataset_path": run_artifacts_summary.get("dataset_path"),
        "python_env": run_artifacts_summary.get("python_env"),
        "time_budget": run_artifacts_summary.get("time_budget"),
        "human_review_policy": resolved_execution_mode,
    }
    repo = _repo_from_sources(source_registry)
    if repo:
        values["repo"] = repo
        values["baseline_repo"] = repo
    if any(item.get("evidence_type") == "paper_summary" for item in usable_evidence):
        values["paper_summary"] = "available"
    return values


def _build_stage_needs(
    task_type: str,
    stage: StageGoal,
    values: dict[str, Any],
    *,
    user_text: str,
) -> list[RequirementNeed]:
    evidence = _evidence_excerpt(user_text)
    needs: list[RequirementNeed] = [
        _need(
            "research_goal", "intent", "plan", "required_now", values.get("research_goal"), "user",
            evidence_quote=evidence,
        ),
        _need(
            "execution_mode", "execution", "plan", "required_now", values.get("execution_mode") or "plan_only",
            values.get("execution_mode_source") or "default",
            evidence_quote=values.get("execution_mode_evidence"),
        ),
        _need("improvement_idea", "intent", "experiment_design", "optional", None, "unknown"),
        _need("target_module", "experiment_object", "patch", "optional", None, "unknown"),
        _need("forbidden_change_scope", "safety", "plan", "auto_fillable", values.get("forbidden_change_scope"), "default"),
    ]
    if values.get("execution_mode_conflict"):
        needs.append(_need(
            "execution_mode_conflict",
            "execution",
            "plan",
            "required_now",
            None,
            "unknown",
            "你同时给出了不同的执行授权，请明确选择仅规划、逐步确认，或确认后协助执行。",
        ))

    if task_type in {"image_anomaly_detection_improvement", "experiment_improvement", "baseline_reproduction"}:
        needs.extend([
            _need("baseline", "experiment_object", "plan", "required_now", values.get("baseline"), "user",
                  evidence_quote=evidence),
            _need("dataset", "experiment_object", "plan", "required_now", values.get("dataset"), "user",
                  evidence_quote=evidence),
            _need("metrics", "evaluation", "plan", "required_now", values.get("metrics"), "user",
                  "你这次主要看哪些评价指标？可以是 image AUROC、pixel AUROC、PRO，或速度/显存。多个指标也可以同时作为核心指标。",
                  evidence_quote=evidence),
            _need("success_criteria", "evaluation", "plan", "required_now", values.get("success_criteria"), "user",
                  evidence_quote=evidence),
            _need("repo", "material", "repo_analysis", "required_later", values.get("repo"), "user"),
            _need("entrypoint", "experiment_object", "repo_analysis", "auto_fillable", values.get("entrypoint"), "repo"),
        ])
    elif task_type == "systems_optimization":
        needs.extend([
            _need("research_object", "experiment_object", "plan", "required_now", values.get("research_object"), "user",
                  "具体要优化哪个系统、算子或运行时对象？", evidence_quote=evidence),
            _need("target_platform", "environment", "plan", "required_now", values.get("target_platform"), "user",
                  "这个优化面向什么目标平台或硬件环境？", evidence_quote=evidence),
            _need("workload", "experiment_object", "plan", "required_now", values.get("workload"), "user",
                  "准备用哪类工作负载或 benchmark 验证？", evidence_quote=evidence),
            _need("metrics", "evaluation", "plan", "required_now", values.get("metrics"), "user",
                  "你准备用哪些性能指标判断优化是否有效？", evidence_quote=evidence),
            _need("success_criteria", "evaluation", "plan", "required_now", values.get("success_criteria"), "user",
                  "达到什么结果算优化成功？", evidence_quote=evidence),
        ])
    elif task_type == "code_diagnosis":
        needs.extend([
            _need("repo", "material", "repo_analysis", "required_now", values.get("repo"), "user"),
            _need("error_log", "material", "chat", "required_now", values.get("error_log"), "user"),
        ])
    else:
        needs.extend([
            _need("research_object", "experiment_object", "plan", "required_now", values.get("research_object"), "user",
                  "这项研究具体面向什么对象或问题？", evidence_quote=evidence),
            _need("material", "material", "chat", "required_later", values.get("paper_summary") or values.get("repo"), "artifact"),
            _need("success_criteria", "evaluation", "plan", "required_now", values.get("success_criteria"), "user",
                  "你期望得到什么结果，达到什么标准算完成？", evidence_quote=evidence),
        ])

    if stage == "run_experiment":
        needs.extend([
            _need("dataset_path", "environment", "run", "required_now", values.get("dataset_path"), "artifact"),
            _need("python_env", "environment", "run", "required_now", values.get("python_env"), "artifact"),
            _need("time_budget", "environment", "run", "required_now", values.get("time_budget"), "user"),
            _need("human_review_policy", "safety", "run", "required_now", values.get("human_review_policy"), "default"),
        ])
    else:
        needs.extend([
            _need("dataset_path", "environment", "run", "required_later", values.get("dataset_path"), "artifact"),
            _need("python_env", "environment", "run", "required_later", values.get("python_env"), "artifact"),
            _need("time_budget", "environment", "run", "required_later", values.get("time_budget"), "user"),
        ])

    return needs


def _need(
    name: str,
    category: NeedCategory,
    required_for: RequiredFor,
    necessity: Necessity,
    value: Any,
    source: NeedSource,
    question: str | None = None,
    *,
    evidence_quote: str | None = None,
) -> RequirementNeed:
    return RequirementNeed(
        name=name,
        category=category,
        required_for=required_for,
        necessity=necessity,
        current_value=value,
        source=source if not _is_empty_need_value(value) else "unknown",
        confidence=0.9 if not _is_empty_need_value(value) else 0.0,
        blocking=False,
        question_to_user=question,
        evidence_quote=evidence_quote if not _is_empty_need_value(value) else None,
    )


def _ensure_stage_required_needs(spec: RequiredNeedSpec) -> None:
    existing = {need.name for need in spec.needs}
    if spec.current_stage_goal == "run_experiment":
        required = [
            ("dataset_path", "environment", "run", "artifact"),
            ("python_env", "environment", "run", "artifact"),
            ("time_budget", "environment", "run", "user"),
            ("human_review_policy", "safety", "run", "default"),
        ]
        for name, category, required_for, source in required:
            if name not in existing:
                spec.needs.append(_need(
                    name,
                    category,  # type: ignore[arg-type]
                    required_for,  # type: ignore[arg-type]
                    "required_now",
                    None,
                    source,  # type: ignore[arg-type]
                ))


def _ensure_profile_required_needs(spec: RequiredNeedSpec) -> None:
    if spec.current_stage_goal not in {"clarify_intent", "generate_plan"}:
        return
    required_by_profile: dict[TaskProfile, list[tuple[str, NeedCategory, str]]] = {
        "empirical_model_research": [
            ("research_goal", "intent", "请说明这项模型实验的主要目标。"),
            ("baseline", "experiment_object", "你希望以哪个方法或实现作为 baseline？"),
            ("dataset", "experiment_object", "准备用哪个数据集评估？"),
            ("metrics", "evaluation", "你准备用哪些指标评价结果？"),
            ("success_criteria", "evaluation", "达到什么结果算实验成功？"),
            ("execution_mode", "execution", "你希望只做规划、逐步确认，还是确认后由助手协助推进？"),
        ],
        "systems_optimization": [
            ("research_goal", "intent", "请说明这项系统优化的主要目标。"),
            ("research_object", "experiment_object", "具体要优化哪个系统、算子或运行时对象？"),
            ("target_platform", "environment", "这个优化面向什么目标平台或硬件环境？"),
            ("workload", "experiment_object", "准备用哪类工作负载或 benchmark 验证？"),
            ("metrics", "evaluation", "你准备用哪些性能指标判断优化是否有效？"),
            ("success_criteria", "evaluation", "达到什么结果算优化成功？"),
            ("execution_mode", "execution", "你希望只做规划、逐步确认，还是确认后由助手协助推进？"),
        ],
        "general_research": [
            ("research_goal", "intent", "请说明这项研究想解决的主要问题。"),
            ("research_object", "experiment_object", "这项研究具体面向什么对象或问题？"),
            ("success_criteria", "evaluation", "你期望得到什么结果，达到什么标准算完成？"),
            ("execution_mode", "execution", "你希望只做规划、逐步确认，还是确认后由助手协助推进？"),
        ],
        "code_diagnosis": [],
    }
    profile_requirements = required_by_profile[spec.task_profile]
    required_names = {name for name, _category, _question in profile_requirements}
    profile_managed_names = {
        name
        for requirements in required_by_profile.values()
        for name, _category, _question in requirements
    }
    for need in spec.needs:
        if need.name in profile_managed_names and need.name not in required_names:
            need.necessity = "optional"
            need.blocking = False
            need.question_to_user = None
    existing = {need.name for need in spec.needs}
    for name, category, question in profile_requirements:
        if name in existing:
            need = next(item for item in spec.needs if item.name == name)
            need.required_for = "plan"
            need.necessity = "required_now"
            if not need.question_to_user:
                need.question_to_user = question
            continue
        spec.needs.append(_need(
            name,
            category,
            "plan",
            "required_now",
            "plan_only" if name == "execution_mode" else None,
            "default" if name == "execution_mode" else "unknown",
            question,
        ))


def _validate_user_evidence(spec: RequiredNeedSpec, user_text: str) -> None:
    for need in spec.needs:
        if need.source not in {"user", "user_confirmed"} or _is_empty_need_value(need.current_value):
            continue
        quote = (need.evidence_quote or "").strip()
        if quote and quote in user_text:
            continue
        need.current_value = None
        need.source = "unknown"
        need.confidence = 0.0
        need.evidence_quote = None
    if spec.task_profile_source in {"user", "user_confirmed"}:
        quote = (spec.task_profile_evidence or "").strip()
        if not quote or quote not in user_text:
            spec.task_profile = "general_research"
            spec.task_profile_source = "unknown"
            spec.task_profile_evidence = None


def _select_task_profile(spec: RequiredNeedSpec, user_text: str) -> None:
    proposed = spec.task_profile
    if (
        not user_text
        and proposed != "general_research"
        and (bool(spec.task_profile_evidence) or _profile_is_corroborated(spec, proposed, user_text))
    ):
        return
    evidence = (spec.task_profile_evidence or "").strip()
    evidence_is_valid = bool(evidence and evidence in user_text)
    fallback_profile = _profile_from_user_text(user_text)
    if (
        proposed != "general_research"
        and evidence_is_valid
        and (_profile_is_corroborated(spec, proposed, user_text) or proposed == fallback_profile)
    ):
        return

    selected = _task_profile_for_type(spec.inferred_task_type)
    if selected != "general_research" and (
        _profile_is_corroborated(spec, selected, user_text) or selected == fallback_profile
    ):
        spec.task_profile = selected
        spec.task_profile_source = "llm_inferred"
        spec.task_profile_evidence = _evidence_excerpt(user_text)
        return
    spec.task_profile = "general_research"
    spec.task_profile_source = "unknown"
    spec.task_profile_evidence = None


def _profile_is_corroborated(spec: RequiredNeedSpec, profile: TaskProfile, user_text: str) -> bool:
    populated = {
        need.name
        for need in spec.needs
        if not _is_empty_need_value(need.current_value)
        and (
            need.source in {"user", "user_confirmed", "artifact", "repo", "paper"}
            or (
                need.source == "llm_inferred"
                and bool(need.evidence_quote)
                and str(need.evidence_quote).strip() in user_text
            )
        )
    }
    if profile == "empirical_model_research":
        return bool(populated & {"baseline", "dataset", "metrics", "target_method"})
    if profile == "systems_optimization":
        return bool(populated & {"research_object", "target_platform", "workload"})
    if profile == "code_diagnosis":
        return spec.inferred_task_type == "code_diagnosis" or bool(populated & {"error_log", "repo"})
    return True


def _profile_from_user_text(user_text: str) -> TaskProfile:
    if not user_text.strip():
        return "general_research"
    values = {
        "baseline": _baseline_from_text(user_text),
        "dataset": _dataset_from_text(user_text),
        "research_object": _research_object_from_text(user_text),
    }
    return _task_profile_for_type(_infer_task_type(user_text, values))


def _is_template_only_need(need: RequirementNeed) -> bool:
    value = str(need.current_value or "").strip()
    if value == "improve primary metric under the same evaluation protocol":
        return True
    if need.source in {"user", "user_confirmed", "artifact", "repo", "paper"}:
        return False
    return value in {
        "提升 baseline 在目标数据集上的表现",
        "improve selected metrics under the same evaluation protocol",
    }


def _infer_task_type(text: str, values: dict[str, Any]) -> str:
    lowered = text.lower()
    if "报错" in text or "bug" in lowered or "traceback" in lowered:
        return "code_diagnosis"
    if "patchcore" in lowered or values.get("baseline") == "PatchCore":
        if "mvtec" in lowered or values.get("dataset") == "MVTec AD":
            return "image_anomaly_detection_improvement"
        return "experiment_improvement"
    if "复现" in text:
        return "baseline_reproduction"
    if values.get("research_object") and _looks_like_systems_optimization(text):
        return "systems_optimization"
    return "general_research"


def _task_profile_for_type(task_type: str) -> TaskProfile:
    if task_type in {"image_anomaly_detection_improvement", "experiment_improvement", "baseline_reproduction"}:
        return "empirical_model_research"
    if task_type == "systems_optimization":
        return "systems_optimization"
    if task_type == "code_diagnosis":
        return "code_diagnosis"
    return "general_research"


def _task_type_for_profile(profile: TaskProfile | None) -> str:
    if profile == "empirical_model_research":
        return "experiment_improvement"
    if profile == "systems_optimization":
        return "systems_optimization"
    if profile == "code_diagnosis":
        return "code_diagnosis"
    return "general_research"


def _task_summary(task_type: str, values: dict[str, Any]) -> str:
    baseline = values.get("baseline")
    dataset = values.get("dataset")
    if baseline and dataset:
        return f"基于 {baseline} 在 {dataset} 上推进研究任务"
    if baseline:
        return f"基于 {baseline} 推进研究任务"
    return task_type


def _goal_from_text(text: str) -> str | None:
    research_object = _research_object_from_text(text)
    if research_object and _looks_like_systems_optimization(text):
        return f"优化 {research_object} 在目标平台和工作负载下的性能"
    if any(token in text for token in ("提升", "优化", "改进", "提高")):
        return "提升 baseline 在目标数据集上的表现"
    if "复现" in text:
        return "复现并评估目标方法"
    if "诊断" in text or "报错" in text:
        return "诊断并修复代码问题"
    if "方案" in text:
        return "整理研究方案"
    return None


def _looks_like_systems_optimization(text: str) -> bool:
    lowered = text.lower()
    return bool(
        re.search(r"(?:ai|人工智能)?\s*算子", lowered)
        or any(term in lowered for term in ("kernel optimization", "compiler optimization", "runtime optimization"))
        or any(term in text for term in ("内核优化", "编译器优化", "运行时优化"))
    )


def _research_object_from_text(text: str) -> str | None:
    patterns = [
        r"(?i)(?:AI|人工智能)?\s*算子",
        r"(?i)attention\s*(?:kernel|算子)?",
        r"(?i)(?:gemm|matmul|convolution|conv)\s*(?:kernel|算子)?",
        r"(?:编译器|运行时|推理引擎)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _target_platform_from_text(text: str) -> str | None:
    patterns = [
        r"(?i)NVIDIA\s+(?:H100|A100|L40S|[A-Z0-9-]+)",
        r"(?i)(?:CUDA|ROCm|x86|ARM)\s*[A-Za-z0-9._-]*",
        r"(?:昇腾|寒武纪|海光)[A-Za-z0-9._-]*",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _workload_from_text(text: str) -> str | None:
    patterns = [
        r"(?i)(?:attention|gemm|matmul|convolution|conv)\s*(?:推理|训练|workload|benchmark|工作负载)",
        r"(?i)(?:MLPerf|SPEC(?:\s+CPU)?|TPC-[A-Z]+)",
        r"(?:推理|训练)(?:服务|工作负载|负载)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def _evidence_excerpt(text: str) -> str | None:
    normalized = text.strip()
    if not normalized:
        return None
    return normalized[-1000:]


def _baseline_from_text(text: str) -> str | None:
    if re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE):
        return "PatchCore"
    return None


def _dataset_from_text(text: str) -> str | None:
    if re.search(r"mvtec\s*(ad)?", text, re.IGNORECASE):
        return "MVTec AD"
    return None


def _success_criteria_from_text(text: str) -> str | None:
    numeric_target = _numeric_improvement_criteria_from_text(text)
    if numeric_target:
        return numeric_target
    if "比" in text and "patchcore" in text.lower() and ("提升" in text or "高于" in text or "超过" in text):
        return "improve selected AUROC metrics over the PatchCore baseline under the same evaluation protocol"
    if "比原始" in text and "提升" in text:
        return "improve selected metrics over the original baseline under the same evaluation protocol"
    if "提升" in text or "提高" in text:
        return "improve selected metrics under the same evaluation protocol"
    if "复现跑通" in text or ("复现" in text and "跑通" in text):
        return "baseline or target method runs reproducibly"
    return None


def _numeric_improvement_criteria_from_text(text: str) -> str | None:
    match = re.search(
        r"(?:提升|提高|增加)[^\d%％]{0,16}([+-]?\d+(?:\.\d+)?)\s*([%％])",
        text,
    )
    if match is None:
        return None
    target = f"{match.group(1)}%"
    return (
        f"在相同评估协议下将选定指标提升 {target}"
        "（按用户原始表述，未指定绝对百分点或相对比例）"
    )


def _execution_mode_observation(text: str) -> tuple[str | None, str | None, bool]:
    phrases: dict[str, tuple[str, ...]] = {
        "plan_only": ("先不要自动改代码", "先帮我整理方案", "只写方案", "plan_only"),
        "approve_each_step": (
            "每步审批",
            "每一步审批",
            "每步确认",
            "每一步确认",
            "逐步确认",
            "代码修改需要逐步确认",
        ),
        "agent_assisted_after_approval": ("允许实验", "自动尝试", "确认后自动执行"),
    }
    matches = [
        (mode, phrase)
        for mode, candidates in phrases.items()
        for phrase in candidates
        if phrase in text
    ]
    modes = {mode for mode, _phrase in matches}
    if len(modes) > 1:
        return None, None, True
    if not matches:
        return None, None, False
    mode, phrase = matches[0]
    return mode, phrase, False


def _apply_execution_authorization_evidence(
    spec: RequiredNeedSpec,
    *,
    current_user_text: str,
    context_text: str,
    existing_execution_mode: Any = None,
) -> None:
    mode, evidence, conflict = _execution_mode_observation(current_user_text)
    source: NeedSource = "user"
    if mode is None and not conflict and existing_execution_mode in {
        "plan_only",
        "approve_each_step",
        "agent_assisted_after_approval",
    }:
        mode = str(existing_execution_mode)
        evidence = None
        source = "artifact"
    elif mode is None and not conflict:
        mode, evidence, conflict = _execution_mode_observation(context_text)
    if conflict:
        if not any(need.name == "execution_mode_conflict" for need in spec.needs):
            spec.needs.append(RequirementNeed(
                name="execution_mode_conflict",
                category="execution",
                required_for="plan",
                necessity="required_now",
                source="unknown",
                question_to_user=(
                    "你同时给出了不同的执行授权，请明确选择仅规划、逐步确认，"
                    "或确认后协助执行。"
                ),
            ))
        return
    if mode is None:
        return
    need = next((item for item in spec.needs if item.name == "execution_mode"), None)
    if need is None:
        spec.needs.append(RequirementNeed(
            name="execution_mode",
            category="execution",
            required_for="plan",
            necessity="required_now",
            current_value=mode,
            source=source,
            confidence=1.0,
            evidence_quote=evidence,
        ))
        return
    need.current_value = mode
    need.source = source
    need.confidence = 1.0
    need.evidence_quote = evidence
    need.question_to_user = None


def _execution_mode_from_text(text: str) -> str:
    mode, _evidence, _conflict = _execution_mode_observation(text)
    return mode or "plan_only"


def _repo_from_sources(sources: list[dict[str, Any]]) -> str | None:
    for source in sources:
        if source.get("kind") == "github_repo":
            return str(source.get("user_label") or source.get("stored_path") or "")
    return None


def _next_best_question(needs: list[RequirementNeed]) -> str | None:
    for need in needs:
        if need.blocking and need.question_to_user:
            return need.question_to_user
    for need in needs:
        if need.blocking:
            return f"请补充 {need.name}。"
    return None


def _has_value(needs: list[RequirementNeed], name: str) -> bool:
    for need in needs:
        if need.name == name and not _is_empty_need_value(need.current_value):
            return True
    return False


def _is_empty_need_value(value: Any) -> bool:
    return value in (None, "", [], {})


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
