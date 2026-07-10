"""Requirement discovery for V2 research conversations.

The resolver decides which facts block the next stage. Rules in this module
canonicalize values and validate readiness; they do not force a single metric
or require optional method-design hints.
"""

from __future__ import annotations

import json
import re
import time
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


class RequiredNeedSpec(BaseModel):
    """Validated requirement state for the current conversation stage."""

    model_config = ConfigDict(extra="forbid")

    task_summary: str = ""
    inferred_task_type: str = "general_research"
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
) -> RequiredNeedSpec:
    """Discover and validate requirements for the current stage.

    `llm_payload` can carry an LLM-produced RequiredNeedSpec JSON. When absent,
    the resolver uses a conservative schema-driven fallback so tests and local
    development do not depend on a live model.
    """

    if llm_payload is not None:
        spec = canonicalize_need_values(RequiredNeedSpec.model_validate(llm_payload))
        _recover_directional_plan_success_criteria(spec, _combined_user_text(user_input, transcript_tail))
        return validate_need_spec(spec)

    text = _combined_user_text(user_input, transcript_tail)
    draft = existing_contract_draft or {}
    sources = source_registry or []
    evidence = usable_evidence or []
    values = _autofill_values(text, draft, sources, evidence, run_artifacts_summary or {})
    task_type = _infer_task_type(text, values)
    needs = _build_stage_needs(task_type, current_stage_goal, values)
    spec = RequiredNeedSpec(
        task_summary=_task_summary(task_type, values),
        inferred_task_type=task_type,
        current_stage_goal=current_stage_goal,
        needs=needs,
    )
    spec = canonicalize_need_values(spec)
    _recover_directional_plan_success_criteria(spec, text)
    return validate_need_spec(spec)


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
    run_dir: Path | None = None,
) -> RequiredNeedSpec:
    """LLM-first requirement discovery with deterministic fallback.

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
    }
    if not api_key:
        return discover_required_needs(**fallback_kwargs)

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
    )
    selector = PromptSelector()
    profile = selector.profile_for_v2_component("need_discovery")
    system_prompt = messages[0]["content"] if messages else ""
    model = "deepseek-v4-flash"

    from autoad_researcher.ui.chat_client import call_research_chat

    started = time.perf_counter()
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=30,
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
        return discover_required_needs(**fallback_kwargs)
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
        return discover_required_needs(**fallback_kwargs)
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
    spec = canonicalize_need_values(spec)
    _recover_directional_plan_success_criteria(spec, _combined_user_text(user_input, transcript_tail))
    return validate_need_spec(spec)


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
    return updated


def validate_need_spec(spec: RequiredNeedSpec) -> RequiredNeedSpec:
    """Validate blocking state and readiness from schema fields."""

    updated = spec.model_copy(deep=True)
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
        need.blocking = need.necessity == "required_now" and _is_empty_need_value(need.current_value)

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
        if need.name == "success_criteria" and _is_empty_need_value(need.current_value):
            need.current_value = user_supported_value
            need.source = "user"
            need.confidence = max(need.confidence, 0.9)
            need.question_to_user = None
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
) -> dict[str, Any]:
    current_metrics = canonicalize_metrics(text)
    current_success_criteria = _success_criteria_from_text(text)
    values: dict[str, Any] = {
        "research_goal": draft.get("research_goal") or _goal_from_text(text),
        "baseline": draft.get("baseline") or _baseline_from_text(text),
        "dataset": draft.get("dataset") or _dataset_from_text(text),
        "metrics": current_metrics or draft.get("primary_metrics") or draft.get("primary_metric"),
        "success_criteria": current_success_criteria or draft.get("success_criteria"),
        "execution_mode": draft.get("execution_mode") or _execution_mode_from_text(text),
        "allowed_change_scope": draft.get("allowed_change_scope"),
        "forbidden_change_scope": draft.get("forbidden_change_scope"),
        "dataset_path": run_artifacts_summary.get("dataset_path"),
        "python_env": run_artifacts_summary.get("python_env"),
        "time_budget": run_artifacts_summary.get("time_budget"),
        "human_review_policy": draft.get("execution_mode") or _execution_mode_from_text(text),
    }
    repo = _repo_from_sources(source_registry)
    if repo:
        values["repo"] = repo
        values["baseline_repo"] = repo
    if any(item.get("evidence_type") == "paper_summary" for item in usable_evidence):
        values["paper_summary"] = "available"
    return values


def _build_stage_needs(task_type: str, stage: StageGoal, values: dict[str, Any]) -> list[RequirementNeed]:
    needs: list[RequirementNeed] = [
        _need("research_goal", "intent", "plan", "required_now", values.get("research_goal"), "user"),
        _need("execution_mode", "execution", "plan", "required_now", values.get("execution_mode") or "plan_only", "user"),
        _need("improvement_idea", "intent", "experiment_design", "optional", None, "unknown"),
        _need("target_module", "experiment_object", "patch", "optional", None, "unknown"),
        _need("forbidden_change_scope", "safety", "plan", "auto_fillable", values.get("forbidden_change_scope"), "default"),
    ]

    if task_type in {"image_anomaly_detection_improvement", "experiment_improvement", "baseline_reproduction"}:
        needs.extend([
            _need("baseline", "experiment_object", "plan", "required_now", values.get("baseline"), "user"),
            _need("dataset", "experiment_object", "plan", "required_now", values.get("dataset"), "user"),
            _need("metrics", "evaluation", "plan", "required_now", values.get("metrics"), "user",
                  "你这次主要看哪些评价指标？可以是 image AUROC、pixel AUROC、PRO，或速度/显存。多个指标也可以同时作为核心指标。"),
            _need("success_criteria", "evaluation", "plan", "required_now", values.get("success_criteria"), "user"),
            _need("repo", "material", "repo_analysis", "required_later", values.get("repo"), "user"),
            _need("entrypoint", "experiment_object", "repo_analysis", "auto_fillable", values.get("entrypoint"), "repo"),
        ])
    elif task_type == "code_diagnosis":
        needs.extend([
            _need("repo", "material", "repo_analysis", "required_now", values.get("repo"), "user"),
            _need("error_log", "material", "chat", "required_now", values.get("error_log"), "user"),
        ])
    else:
        needs.extend([
            _need("material", "material", "chat", "required_later", values.get("paper_summary") or values.get("repo"), "artifact"),
            _need("success_criteria", "evaluation", "plan", "required_now", values.get("success_criteria"), "user"),
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
    if any(token in text for token in ("提升", "优化", "改进", "提高")):
        return "提升 baseline 在目标数据集上的表现"
    if "复现" in text:
        return "复现并评估目标方法"
    if "诊断" in text or "报错" in text:
        return "诊断并修复代码问题"
    if "方案" in text:
        return "整理研究方案"
    return None


def _baseline_from_text(text: str) -> str | None:
    if re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE):
        return "PatchCore"
    return None


def _dataset_from_text(text: str) -> str | None:
    if re.search(r"mvtec\s*(ad)?", text, re.IGNORECASE):
        return "MVTec AD"
    return None


def _success_criteria_from_text(text: str) -> str | None:
    if "比" in text and "patchcore" in text.lower() and ("提升" in text or "高于" in text or "超过" in text):
        return "improve selected AUROC metrics over the PatchCore baseline under the same evaluation protocol"
    if "比原始" in text and "提升" in text:
        return "improve selected metrics over the original baseline under the same evaluation protocol"
    if "提升" in text or "提高" in text:
        return "improve selected metrics under the same evaluation protocol"
    if "复现跑通" in text or ("复现" in text and "跑通" in text):
        return "baseline or target method runs reproducibly"
    return None


def _execution_mode_from_text(text: str) -> str:
    if any(token in text for token in ("先不要自动改代码", "先帮我整理方案", "只写方案", "plan_only")):
        return "plan_only"
    if any(token in text for token in ("每步审批", "每一步审批", "每步确认")):
        return "approve_each_step"
    if any(token in text for token in ("允许实验", "自动尝试")):
        return "agent_assisted_after_approval"
    return "plan_only"


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
