"""Research intent contract builder for HF-2/V2.

HF-2 captures user intent and experiment boundaries. It does not require the
user to design methods or pick target modules; later experiment agents do that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_typed_event
from autoad_researcher.source_normalizer import extract_first_url
from autoad_researcher.assistant.v2.need_discovery import (
    RequiredNeedSpec,
    canonicalize_metrics,
    discover_required_needs_with_llm,
    validate_need_spec,
)


CONTRACT_DRAFT_FILE = "research_intent_contract_draft.json"
CONTRACT_FILE = "research_intent_contract.json"

DEFAULT_ALLOWED_CHANGE_SCOPE = [
    "model",
    "training",
    "loss",
    "feature",
    "sampling",
    "postprocess",
    "config",
    "scheduler",
    "augmentation",
]

DEFAULT_FORBIDDEN_CHANGE_SCOPE = [
    "modify_test_labels",
    "change_test_split",
    "train_on_test_set",
    "leak_ground_truth",
    "change_metric_definition",
    "delete_hard_samples",
    "report_unreproducible_best_only",
]

CORE_REQUIRED_FIELDS = [
    "research_goal",
    "baseline",
    "dataset",
    "primary_metrics",
    "success_criteria",
    "execution_mode",
]


def missing_contract_planning_fields(contract: "ResearchIntentContract") -> list[str]:
    """Recompute deterministic planning requirements from canonical fields."""
    return _missing_core_fields(contract)


class MetricMention(BaseModel):
    """A metric phrase normalized to an internal metric id."""

    model_config = ConfigDict(extra="forbid")

    raw_text: str
    canonical: str
    role: Literal["primary_candidate", "secondary_candidate", "mentioned"] = "mentioned"
    evidence: str | None = None
    confidence: float | None = None


class MetricIntent(BaseModel):
    """Metric intent extracted from user text.

    Rules only canonicalize metric names. Ambiguous multi-metric "main" phrases
    are treated as co-primary instead of forcing a single winner by position.
    """

    model_config = ConfigDict(extra="forbid")

    mentioned_metrics: list[MetricMention] = Field(default_factory=list)
    primary_metrics: list[str] = Field(default_factory=list)
    secondary_metrics: list[str] = Field(default_factory=list)
    metric_priority: str | None = None
    needs_user_confirmation: bool = False
    clarifying_question: str | None = None
    extraction_source: Literal["llm", "fallback", "user_confirmed"] = "fallback"


class ResearchIntentContract(BaseModel):
    """Intent contract for downstream plan/repo/experiment agents."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    run_id: str

    task_domain: str | None = "anomaly_detection"
    research_goal: str | None = None
    baseline: str | None = None
    baseline_repo: str | None = None
    baseline_commit: str | None = None
    baseline_entrypoint: str | None = None
    baseline_config: str | None = None

    dataset: str | None = None
    evaluation_protocol: str | None = None
    primary_metrics: list[str] = Field(default_factory=list)
    primary_metric: str | None = None
    secondary_metrics: list[str] = Field(default_factory=list)
    metric_priority: str | None = None
    metric_intent: MetricIntent = Field(default_factory=MetricIntent)
    success_criteria: str | None = None

    compute_environment: dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"] = "plan_only"

    user_improvement_hints: list[str] = Field(default_factory=list)
    user_target_module_hints: list[str] = Field(default_factory=list)
    preferred_method_hints: list[str] = Field(default_factory=list)
    risk_preference: str | None = None

    allowed_change_scope: list[str] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_CHANGE_SCOPE))
    forbidden_change_scope: list[str] = Field(default_factory=lambda: list(DEFAULT_FORBIDDEN_CHANGE_SCOPE))

    evidence_sources: list[dict[str, Any]] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    user_confirmed_fields: list[str] = Field(default_factory=list)
    need_spec: RequiredNeedSpec = Field(default_factory=RequiredNeedSpec)

    ready_for_plan: bool = False
    ready_for_repo_analysis: bool = False
    ready_for_experiment_agents: bool = False


def build_contract_from_context(
    *,
    run_dir: Path,
    user_input: str,
    llm_context: dict[str, Any],
    transcript_tail: list[dict[str, Any]] | None = None,
    existing_contract_draft: ResearchIntentContract | None = None,
    api_key: str = "",
    provider_url: str = "",
) -> ResearchIntentContract:
    """Build a deterministic draft from confirmed chat facts and artifacts."""

    confirmed = dict(llm_context.get("confirmed_from_user") or {})
    combined_user_text = _filtered_user_text(user_input, transcript_tail)
    sources = _load_source_registry_sources(run_dir)
    need_spec = discover_required_needs_with_llm(
        user_input=user_input,
        transcript_tail=transcript_tail,
        existing_contract_draft=(
            existing_contract_draft.model_dump(mode="json") if existing_contract_draft is not None else None
        ),
        source_registry=sources,
        usable_evidence=llm_context.get("usable_evidence", []) or [],
        current_stage_goal="generate_plan",
        answerability=llm_context.get("answerability", {}) or {},
        api_key=api_key,
        provider_url=provider_url,
        run_dir=run_dir,
    )
    _append_need_discovery_decided_event(run_dir, need_spec)

    need_fields = contract_fields_from_need_spec(need_spec)
    need_primary_metrics = _canonicalize_metric_list(_listify(need_fields.get("primary_metrics")))
    need_secondary_metrics = _canonicalize_metric_list(_listify(need_fields.get("secondary_metrics")))
    confirmed_metrics = _canonicalize_metric_list(_listify(confirmed.get("metrics")))
    inferred_metric_intent = _extract_metric_intent(combined_user_text)
    if need_primary_metrics:
        metric_intent = MetricIntent(
            mentioned_metrics=[
                MetricMention(
                    raw_text=metric,
                    canonical=metric,
                    role="primary_candidate",
                    confidence=1.0,
                )
                for metric in [*need_primary_metrics, *need_secondary_metrics]
            ],
            primary_metrics=need_primary_metrics,
            secondary_metrics=[metric for metric in need_secondary_metrics if metric not in need_primary_metrics],
            metric_priority=_clean_str(need_fields.get("metric_priority")) or _metric_priority(
                need_primary_metrics,
                need_secondary_metrics,
            ),
            extraction_source=_metric_intent_source_from_need_spec(need_spec),
        )
    elif confirmed_metrics:
        metric_intent = MetricIntent(
            mentioned_metrics=[
                MetricMention(raw_text=metric, canonical=metric, role="primary_candidate", confidence=1.0)
                for metric in confirmed_metrics
            ],
            primary_metrics=confirmed_metrics,
            secondary_metrics=[],
            metric_priority="fallback_user_history",
            extraction_source="fallback",
        )
    else:
        metric_intent = inferred_metric_intent
    primary_metrics = _expand_contextual_metric_set(metric_intent.primary_metrics, combined_user_text)
    if primary_metrics != metric_intent.primary_metrics:
        metric_intent = metric_intent.model_copy(update={
            "primary_metrics": primary_metrics,
            "secondary_metrics": [metric for metric in metric_intent.secondary_metrics if metric not in primary_metrics],
            "metric_priority": _metric_priority(primary_metrics, []),
        })
    primary_metric = primary_metrics[0] if len(primary_metrics) == 1 else None
    baseline_repo = extract_first_url(str(need_fields.get("baseline_repo", ""))) or _first_github_source(sources) or _clean_str(need_fields.get("baseline_repo"))
    success_criteria = _normalize_success_criteria(
        _clean_str(need_fields.get("success_criteria")) or _infer_success_criteria(combined_user_text, primary_metrics),
        combined_user_text,
        primary_metrics,
    )
    baseline = _clean_str(confirmed.get("baseline")) or _clean_str(need_fields.get("baseline")) or _infer_baseline(combined_user_text)
    dataset = _clean_str(confirmed.get("dataset")) or _clean_str(need_fields.get("dataset")) or _infer_dataset(combined_user_text)
    research_goal = _normalize_research_goal(
        _clean_str(confirmed.get("research_goal"))
        or _clean_str(need_fields.get("research_goal"))
        or _infer_research_goal(combined_user_text, confirmed),
        baseline,
        dataset,
        primary_metrics,
    )

    contract = ResearchIntentContract(
        run_id=run_dir.name,
        task_domain=_task_domain_from_need_spec(need_spec) or _infer_task_domain(combined_user_text),
        research_goal=research_goal,
        baseline=baseline,
        baseline_repo=baseline_repo,
        dataset=dataset,
        evaluation_protocol=_clean_str(need_fields.get("evaluation_protocol")) or _infer_evaluation_protocol(combined_user_text),
        primary_metrics=primary_metrics,
        primary_metric=primary_metric,
        secondary_metrics=metric_intent.secondary_metrics,
        metric_priority=metric_intent.metric_priority,
        metric_intent=metric_intent,
        success_criteria=success_criteria,
        compute_environment=_infer_compute_environment(combined_user_text, confirmed),
        execution_mode=_contract_execution_mode(need_fields.get("execution_mode"), combined_user_text),
        user_improvement_hints=_infer_improvement_hints(combined_user_text, llm_context.get("usable_evidence", []) or []),
        user_target_module_hints=_infer_target_module_hints(combined_user_text),
        preferred_method_hints=_infer_preferred_method_hints(combined_user_text, llm_context.get("usable_evidence", []) or []),
        risk_preference=_infer_risk_preference(combined_user_text),
        evidence_sources=_contract_evidence_sources(sources, llm_context),
        need_spec=need_spec,
    )
    _refresh_contract_state(contract)
    return contract


def merge_contract_draft(
    existing: ResearchIntentContract | None,
    update: ResearchIntentContract,
) -> ResearchIntentContract:
    """Merge a new deterministic observation into the existing draft.

    Non-empty existing fields are retained unless the latest user turn produced
    a non-empty replacement. Derived readiness fields are recalculated from the
    merged canonical contract.
    """

    if existing is None:
        merged = update.model_copy(deep=True)
        _refresh_contract_state(merged)
        return merged

    merged = existing.model_copy(deep=True)
    update_data = update.model_dump(mode="python")

    scalar_fields = [
        "task_domain",
        "research_goal",
        "baseline",
        "baseline_repo",
        "baseline_commit",
        "baseline_entrypoint",
        "baseline_config",
        "dataset",
        "evaluation_protocol",
        "metric_priority",
        "primary_metric",
        "success_criteria",
        "risk_preference",
        "execution_mode",
    ]
    for field in scalar_fields:
        value = update_data.get(field)
        if value not in (None, "", [], {}):
            if _should_keep_existing_field(merged, update, field):
                continue
            setattr(merged, field, value)

    dict_fields = ["compute_environment"]
    for field in dict_fields:
        current = getattr(merged, field)
        value = update_data.get(field)
        if isinstance(value, dict) and value:
            setattr(merged, field, {**current, **value})

    list_fields = [
        "secondary_metrics",
        "primary_metrics",
        "user_improvement_hints",
        "user_target_module_hints",
        "preferred_method_hints",
        "allowed_change_scope",
        "forbidden_change_scope",
        "user_confirmed_fields",
    ]
    for field in list_fields:
        setattr(merged, field, _merge_unique_list(getattr(merged, field), update_data.get(field)))

    if update.metric_intent.mentioned_metrics:
        merged.metric_intent = _merge_metric_intent(merged.metric_intent, update.metric_intent)
        merged.primary_metrics = merged.metric_intent.primary_metrics
        merged.secondary_metrics = merged.metric_intent.secondary_metrics
        merged.metric_priority = merged.metric_intent.metric_priority
        merged.primary_metric = merged.primary_metrics[0] if len(merged.primary_metrics) == 1 else None
    if update.need_spec.needs:
        merged.need_spec = update.need_spec

    merged.evidence_sources = _merge_evidence_sources(merged.evidence_sources, update.evidence_sources)
    _refresh_contract_state(merged)
    return merged


def contract_fields_from_need_spec(spec: RequiredNeedSpec) -> dict[str, Any]:
    """Map validated NeedSpec values into ResearchIntentContract fields.

    Deterministic inference remains a fallback. This bridge lets LLM-discovered
    non-hardcoded tasks such as EfficientAD/VisA enter the canonical contract.
    """

    candidates: dict[str, tuple[int, Any]] = {}
    for need in spec.needs:
        if need.current_value in (None, "", [], {}):
            continue
        field = _contract_field_for_need_name(need.name)
        if field is None:
            continue
        value = _contract_value_from_need(need.name, need.current_value)
        if value in (None, "", [], {}):
            continue
        priority = _need_source_priority(need.source)
        existing = candidates.get(field)
        if existing is None or priority > existing[0]:
            candidates[field] = (priority, value)

    fields = {field: value for field, (_priority, value) in candidates.items()}
    if fields.get("primary_metrics") and "metric_priority" not in fields:
        primary_metrics = _listify(fields.get("primary_metrics"))
        secondary_metrics = _listify(fields.get("secondary_metrics"))
        fields["metric_priority"] = _metric_priority(primary_metrics, secondary_metrics)
    return fields


def save_contract_draft(run_dir: Path, contract: ResearchIntentContract) -> Path:
    from autoad_researcher.core.control_plane.io import atomic_write_json
    from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork

    path = run_dir / CONTRACT_DRAFT_FILE
    with ControlPlaneUnitOfWork(run_dir):
        atomic_write_json(path, contract.model_dump(mode="json"))
    _append_contract_draft_updated_event(run_dir, contract)
    return path


def save_confirmed_contract(run_dir: Path, contract: ResearchIntentContract) -> Path:
    from autoad_researcher.assistant.v2.contract_hashing import confirmed_contract_sha256
    from autoad_researcher.core.control_plane.errors import CorruptAuthoritativeStore
    from autoad_researcher.core.control_plane.io import atomic_write_json
    from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork

    path = run_dir / CONTRACT_FILE
    with ControlPlaneUnitOfWork(run_dir):
        existing = _load_contract(path)
        if path.is_file() and existing is None:
            raise CorruptAuthoritativeStore(f"invalid confirmed contract: {path}")
        if existing is not None:
            if confirmed_contract_sha256(existing) != confirmed_contract_sha256(contract):
                raise CorruptAuthoritativeStore("one run cannot replace its confirmed contract")
            return path
        atomic_write_json(path, contract.model_dump(mode="json"))
    return path


def load_contract_draft(run_dir: Path) -> ResearchIntentContract | None:
    return _load_contract(run_dir / CONTRACT_DRAFT_FILE)


def load_confirmed_contract(run_dir: Path) -> ResearchIntentContract | None:
    return _load_contract(run_dir / CONTRACT_FILE)


def is_contract_confirmation(user_input: str) -> bool:
    text = re.sub(r"[\s。！!？?，,；;：:]+", "", user_input.strip().lower())
    if any(token in text for token in ("不确认", "别确认", "先不确认", "不是")):
        return False
    return text in {"确认", "可以", "没问题", "同意", "就这样", "确认合同", "确认目标"}


def format_contract_for_user(contract: ResearchIntentContract) -> str:
    """Render a compact confirmation text without method-design pressure."""

    lines = [
        "我整理到的研究意图合同如下：",
        f"- task domain：{contract.task_domain or '待确认'}",
        f"- 研究目标：{contract.research_goal or '待确认'}",
        f"- baseline：{contract.baseline or '待确认'}",
        f"- baseline repo：{contract.baseline_repo or '未提供，可后续由 repo analyzer 补'}",
        f"- baseline commit：{contract.baseline_commit or '未提供'}",
        f"- baseline entrypoint：{contract.baseline_entrypoint or '未提供'}",
        f"- baseline config：{contract.baseline_config or '未提供'}",
        f"- dataset：{contract.dataset or '待确认'}",
        f"- primary metrics：{', '.join(contract.primary_metrics) if contract.primary_metrics else '待确认'}",
        f"- secondary metrics：{', '.join(contract.secondary_metrics) if contract.secondary_metrics else '未指定'}",
        f"- metric priority：{contract.metric_priority or '未指定'}",
        f"- success criteria：{contract.success_criteria or '待确认'}",
        f"- execution mode：{contract.execution_mode}",
        f"- evaluation protocol：{contract.evaluation_protocol or '可后续由 repo/实验 agents 补全'}",
        f"- compute environment：{contract.compute_environment or '可后续由环境检测补全'}",
        f"- risk preference：{contract.risk_preference or '未指定'}",
        "- allowed boundary：" + ", ".join(contract.allowed_change_scope),
        "- forbidden boundary：" + ", ".join(contract.forbidden_change_scope),
    ]
    if contract.user_improvement_hints:
        lines.append("- 你的改进想法 hint：" + "；".join(contract.user_improvement_hints))
    else:
        lines.append("- 改进想法 hint：未提供；这不阻塞，后续 experiment agents 会自动探索。")
    if contract.user_target_module_hints:
        lines.append("- 目标模块 hint：" + "；".join(contract.user_target_module_hints))
    else:
        lines.append("- 目标模块 hint：未提供；这不阻塞，后续 repo/experiment agents 会定位。")
    if contract.preferred_method_hints:
        lines.append("- 偏好方法 hint：" + "；".join(contract.preferred_method_hints))
    else:
        lines.append("- 偏好方法 hint：未提供。")
    if contract.missing_required_fields:
        lines.append("还缺少：" + ", ".join(contract.missing_required_fields))
        lines.append("你可以先回答最关键的一项：主要想优化指标、速度、显存、训练成本、复现跑通，还是稳定性/泛化？")
    else:
        lines.append(
            "如果以上正确，请在确认弹窗中点击“确认合同”。确认后会保存合同并创建实验准备任务；"
            "不会修改代码、创建 worktree、运行 baseline 或占用 GPU。"
        )
    return "\n".join(lines)


def _append_need_discovery_decided_event(run_dir: Path, spec: RequiredNeedSpec) -> None:
    append_typed_event(run_dir, "planner.need_discovery.decided", {
        "current_stage_goal": spec.current_stage_goal,
        "inferred_task_type": spec.inferred_task_type,
        "need_count": len(spec.needs),
        "blocking_needs": list(spec.blocking_needs),
        "ready_for_plan": spec.ready_for_plan,
        "ready_for_repo_analysis": spec.ready_for_repo_analysis,
        "ready_for_experiment_design": spec.ready_for_experiment_design,
        "ready_for_patch": spec.ready_for_patch,
        "ready_for_run": spec.ready_for_run,
    })


def _append_contract_draft_updated_event(run_dir: Path, contract: ResearchIntentContract) -> None:
    populated_fields = [
        field
        for field in CORE_REQUIRED_FIELDS
        if getattr(contract, field, None) not in (None, "", [], {})
    ]
    append_typed_event(run_dir, "contract.draft.updated", {
        "schema_version": contract.schema_version,
        "populated_required_fields": populated_fields,
        "missing_required_fields": list(contract.missing_required_fields),
        "ready_for_plan": contract.ready_for_plan,
        "ready_for_repo_analysis": contract.ready_for_repo_analysis,
        "ready_for_experiment_agents": contract.ready_for_experiment_agents,
        "primary_metrics_count": len(contract.primary_metrics),
        "evidence_source_count": len(contract.evidence_sources),
    })


def _load_contract(path: Path) -> ResearchIntentContract | None:
    if not path.is_file():
        return None
    try:
        return ResearchIntentContract.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _combined_user_text(user_input: str, transcript_tail: list[dict[str, Any]] | None) -> str:
    parts = [
        str(entry.get("content", ""))
        for entry in (transcript_tail or [])
        if entry.get("role") == "user"
    ]
    parts.append(user_input)
    return "\n".join(part for part in parts if part.strip())


def _filtered_user_text(user_input: str, transcript_tail: list[dict[str, Any]] | None) -> str:
    """Combined user text with non-research content filtered out.

    Strips identity questions, confirmations, source intake commands,
    and plain greetings that should not be used as contract field values.
    """
    raw = _combined_user_text(user_input, transcript_tail)
    lines = raw.split("\n")
    filtered: list[str] = []
    skip_patterns = (
        r"^(你是谁|我是谁|我是人类!?|我是傻逼|你好|哈哈|你真聪明|你能做什么)",
        r"^(确认|可以|没问题|就这样|同意|按这个来)$",
        r"^(请读取|下载并解析|分析这个仓库|这是 baseline)",
        r"^https?://",
        r"^搜索 ",
    )
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(re.match(p, stripped, re.IGNORECASE) for p in skip_patterns):
            continue
        filtered.append(stripped)
    return "\n".join(filtered)


def _load_source_registry_sources(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "sources" / "source_references.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    sources = payload.get("sources", [])
    return [source for source in sources if isinstance(source, dict)] if isinstance(sources, list) else []


def _first_github_source(sources: list[dict[str, Any]]) -> str | None:
    for source in sources:
        if source.get("kind") == "github_repo":
            return _clean_str(source.get("user_label")) or _clean_str(source.get("stored_path"))
    return None


def _contract_evidence_sources(sources: list[dict[str, Any]], llm_context: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for source in sources:
        evidence.append({
            "source_id": source.get("source_id"),
            "kind": source.get("kind"),
            "status": source.get("status"),
            "label": source.get("user_label"),
        })
    for item in llm_context.get("usable_evidence", []) or []:
        if isinstance(item, dict):
            evidence.append({
                "source_id": item.get("source_id"),
                "artifact_path": item.get("artifact_path"),
                "evidence_type": item.get("evidence_type"),
                "support_level": item.get("support_level"),
            })
    return evidence


def _contract_field_for_need_name(name: str) -> str | None:
    mapping = {
        "research_goal": "research_goal",
        "baseline": "baseline",
        "target_method": "baseline",
        "dataset": "dataset",
        "metrics": "primary_metrics",
        "primary_metrics": "primary_metrics",
        "secondary_metrics": "secondary_metrics",
        "metric_priority": "metric_priority",
        "success_criteria": "success_criteria",
        "execution_mode": "execution_mode",
        "repo": "baseline_repo",
        "baseline_repo": "baseline_repo",
        "evaluation_protocol": "evaluation_protocol",
    }
    return mapping.get(name)


def _contract_value_from_need(name: str, value: Any) -> Any:
    if name in {"metrics", "primary_metrics", "secondary_metrics"}:
        return _canonicalize_metric_list(_listify(value))
    if isinstance(value, str):
        return value.strip() or None
    return value


def _need_source_priority(source: str) -> int:
    return {
        "user_confirmed": 60,
        "user": 50,
        "artifact": 40,
        "repo": 40,
        "paper": 40,
        "llm_inferred": 30,
        "default": 20,
        "unknown": 10,
    }.get(source, 0)


def _field_source_priority(spec: RequiredNeedSpec, field: str) -> int:
    priority = 0
    for need in spec.needs:
        mapped = _contract_field_for_need_name(need.name)
        if mapped == field and need.current_value not in (None, "", [], {}):
            priority = max(priority, _need_source_priority(need.source))
    return priority


def _should_keep_existing_field(existing: ResearchIntentContract, update: ResearchIntentContract, field: str) -> bool:
    existing_value = getattr(existing, field, None)
    if existing_value in (None, "", [], {}):
        return False
    existing_priority = _field_source_priority(existing.need_spec, field)
    update_priority = _field_source_priority(update.need_spec, field)
    return existing_priority > update_priority > 0


def _metric_intent_source_from_need_spec(spec: RequiredNeedSpec) -> Literal["llm", "fallback", "user_confirmed"]:
    priority = _field_source_priority(spec, "primary_metrics")
    if priority >= _need_source_priority("user_confirmed"):
        return "user_confirmed"
    if priority >= _need_source_priority("llm_inferred"):
        return "llm"
    return "fallback"


def _task_domain_from_need_spec(spec: RequiredNeedSpec) -> str | None:
    task_type = spec.inferred_task_type.lower()
    if "anomaly" in task_type:
        return "anomaly_detection"
    if task_type and task_type != "general_research":
        return task_type
    return None


def _contract_execution_mode(
    value: Any,
    text: str,
) -> Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]:
    cleaned = _clean_str(value)
    if cleaned in {"plan_only", "approve_each_step", "agent_assisted_after_approval"}:
        return cleaned  # type: ignore[return-value]
    return _infer_execution_mode(text)


def _infer_task_domain(text: str) -> str:
    lowered = text.lower()
    if "异常检测" in text or "anomaly" in lowered or "mvtec" in lowered or "patchcore" in lowered:
        return "anomaly_detection"
    return "deep_learning"


def _infer_research_goal(text: str, confirmed: dict[str, Any]) -> str | None:
    if _clean_str(confirmed.get("research_goal")):
        return _clean_str(confirmed.get("research_goal"))
    if any(token in text for token in ("提升", "优化", "改进", "提高")):
        if "速度" in text or "推理" in text:
            return "提升 baseline 推理速度"
        if "显存" in text:
            return "降低 baseline 显存占用"
        if "复现" in text and "跑通" in text:
            return "复现并跑通 baseline"
        baseline = _infer_baseline(text)
        dataset = _infer_dataset(text)
        metric_intent = _extract_metric_intent(text)
        if baseline and dataset and metric_intent.primary_metrics:
            metric_text = ", ".join(metric_intent.primary_metrics)
            return f"提升 {baseline} 在 {dataset} 上的 {metric_text}"
        return "提升 baseline 在目标数据集上的表现"
    if "复现" in text:
        return "复现并评估目标方法"
    return None


def _normalize_research_goal(
    value: str | None,
    baseline: str | None,
    dataset: str | None,
    primary_metrics: list[str],
) -> str | None:
    if baseline and dataset and primary_metrics and value in {
        None,
        "",
        "提升 baseline 在目标数据集上的表现",
    }:
        metric_text = ", ".join(primary_metrics)
        return f"提升 {baseline} 在 {dataset} 上的 {metric_text}"
    return value


def _infer_baseline(text: str) -> str | None:
    if re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE):
        return "PatchCore"
    return None


def _infer_dataset(text: str) -> str | None:
    if re.search(r"mvtec\s*(ad)?", text, re.IGNORECASE):
        return "MVTec AD"
    return None


def _infer_primary_metric(text: str) -> str | None:
    metrics = _extract_metric_intent(text).primary_metrics
    return metrics[0] if len(metrics) == 1 else None


def _infer_metrics(text: str) -> tuple[str | None, list[str]]:
    intent = _extract_metric_intent(text)
    primary = intent.primary_metrics[0] if len(intent.primary_metrics) == 1 else None
    return primary, intent.secondary_metrics


def _extract_metric_intent(text: str) -> MetricIntent:
    mentions = _find_metric_mentions(text)
    mentioned = _unique_metrics([mention.canonical for mention in mentions])
    if _requests_two_common_auroc_metrics(text):
        mentioned = _unique_metrics([*mentioned, "image_level_auroc", "pixel_level_auroc"])
    if not mentioned:
        return MetricIntent()

    explicit_primary = _metrics_with_primary_cues(text, mentioned)
    explicit_secondary = _metrics_with_secondary_cues(text, mentioned)

    if explicit_primary:
        primary_metrics = explicit_primary
        secondary_metrics = explicit_secondary or [metric for metric in mentioned if metric not in primary_metrics]
    elif explicit_secondary:
        secondary_metrics = explicit_secondary
        primary_metrics = [metric for metric in mentioned if metric not in secondary_metrics]
        if not primary_metrics:
            primary_metrics = mentioned
            secondary_metrics = []
    else:
        primary_metrics = mentioned
        secondary_metrics = []

    primary_metrics = _unique_metrics(primary_metrics)
    secondary_metrics = [metric for metric in _unique_metrics(secondary_metrics) if metric not in primary_metrics]
    metric_priority = _metric_priority(primary_metrics, secondary_metrics)
    role_by_metric = {
        **{metric: "primary_candidate" for metric in primary_metrics},
        **{metric: "secondary_candidate" for metric in secondary_metrics},
    }
    normalized_mentions = [
        mention.model_copy(update={"role": role_by_metric.get(mention.canonical, "mentioned")})
        for mention in mentions
    ]
    return MetricIntent(
        mentioned_metrics=normalized_mentions,
        primary_metrics=primary_metrics,
        secondary_metrics=secondary_metrics,
        metric_priority=metric_priority,
        needs_user_confirmation=False,
        extraction_source="fallback",
    )


def _find_metric_mentions(text: str) -> list[MetricMention]:
    patterns = [
        ("image_level_auroc", r"image[-_\s]*(level[-_\s]*)?(auc|auroc)|instance[-_\s]*(level[-_\s]*)?(auc|auroc)"),
        ("pixel_level_auroc", r"pixel[-_\s]*(level[-_\s]*)?(auc|auroc)|full[-_\s]*pixel[-_\s]*(auc|auroc)|定位"),
        ("pro", r"\bpro\b|per[-_\s]*region[-_\s]*overlap|\bau[-_\s]*pro\b"),
        ("f1", r"\bf1\b|f1[-_\s]*score"),
        ("accuracy", r"accuracy|准确率"),
        ("inference_latency", r"速度|推理速度|latency|throughput|fps"),
        ("peak_vram", r"显存|memory|vram"),
        ("image_level_auroc", r"(?<![A-Za-z0-9_])auroc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc-?roc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc(?![A-Za-z0-9_])"),
    ]
    mentions: list[tuple[int, MetricMention]] = []
    occupied_spans: list[tuple[int, int]] = []
    for canonical, pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in occupied_spans):
                continue
            occupied_spans.append((start, end))
            mentions.append((
                start,
                MetricMention(
                    raw_text=match.group(0),
                    canonical=canonical,
                    evidence=_clause_containing(text, start),
                    confidence=0.9,
                ),
            ))
    return [mention for _, mention in sorted(mentions, key=lambda item: item[0])]


def _requests_two_common_auroc_metrics(text: str) -> bool:
    lowered = text.lower()
    return (
        bool(re.search(r"(?<![A-Za-z0-9_])auroc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc-?roc(?![A-Za-z0-9_])", lowered))
        and any(token in text for token in ("两种", "两个", "主流"))
        and any(token in lowered for token in ("mvtec", "patchcore", "anomaly"))
    )


def _expand_contextual_metric_set(metrics: list[str], text: str) -> list[str]:
    if _requests_two_common_auroc_metrics(text):
        return _unique_metrics([*metrics, "image_level_auroc", "pixel_level_auroc"])
    return metrics


def _metrics_with_primary_cues(text: str, metrics: list[str]) -> list[str]:
    primary: list[str] = []
    for clause in _metric_clauses(text):
        clause_metrics = _unique_metrics([mention.canonical for mention in _find_metric_mentions(clause)])
        if not clause_metrics:
            continue
        if any(token in clause for token in ("为主", "主指标", "第一优先", "最优先", "优先", "重点")):
            primary.extend(metric for metric in clause_metrics if metric in metrics)
        if re.search(r"\bprimary\b", clause, flags=re.IGNORECASE):
            primary.extend(metric for metric in clause_metrics if metric in metrics)
    return _unique_metrics(primary)


def _metrics_with_secondary_cues(text: str, metrics: list[str]) -> list[str]:
    secondary: list[str] = []
    for clause in _metric_clauses(text):
        clause_metrics = _unique_metrics([mention.canonical for mention in _find_metric_mentions(clause)])
        if not clause_metrics:
            continue
        if any(token in clause for token in ("参考", "辅助", "次要", "也记录", "也看", "guardrail")):
            secondary.extend(metric for metric in clause_metrics if metric in metrics)
        if re.search(r"\bsecondary\b", clause, flags=re.IGNORECASE):
            secondary.extend(metric for metric in clause_metrics if metric in metrics)
    return _unique_metrics(secondary)


def _metric_clauses(text: str) -> list[str]:
    return [clause.strip() for clause in re.split(r"[，,。；;\n]+", text) if clause.strip()]


def _clause_containing(text: str, position: int) -> str:
    start = max(text.rfind(separator, 0, position) for separator in ("，", ",", "。", "；", ";", "\n")) + 1
    ends = [text.find(separator, position) for separator in ("，", ",", "。", "；", ";", "\n")]
    valid_ends = [end for end in ends if end != -1]
    end = min(valid_ends) if valid_ends else len(text)
    return text[start:end].strip()


def _metric_priority(primary_metrics: list[str], secondary_metrics: list[str]) -> str | None:
    if len(primary_metrics) > 1 and not secondary_metrics:
        return "co_primary"
    if len(primary_metrics) == 1 and secondary_metrics:
        return f"{primary_metrics[0]}_first"
    if len(primary_metrics) == 1:
        return "single_primary"
    return None


def _infer_success_criteria(text: str, primary_metrics: list[str]) -> str | None:
    if "复现跑通" in text or ("复现" in text and "跑通" in text):
        return "baseline or target method runs reproducibly"
    if "显存" in text:
        return "reduce peak VRAM without weakening the evaluation protocol"
    if "速度" in text or "推理" in text:
        return "reduce inference latency without weakening the evaluation protocol"
    if "比" in text and re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE) and ("提升" in text or "高于" in text or "超过" in text):
        metric = ", ".join(primary_metrics) if primary_metrics else "AUROC"
        return f"improve {metric} over the PatchCore baseline under the same evaluation protocol"
    if "提升" in text or "提高" in text or "优化" in text:
        metric = ", ".join(primary_metrics) if primary_metrics else "primary metric"
        return f"improve {metric} under the same evaluation protocol"
    return None


def _normalize_success_criteria(value: str | None, text: str, primary_metrics: list[str]) -> str | None:
    if value in (None, ""):
        return None
    metric = ", ".join(primary_metrics) if primary_metrics else "AUROC"
    if (
        len(value) > 160
        or "\n" in value
        or ("成功标准" in value and len(value) > 40)
        or "selected metrics" in value
        or ("比" in text and re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE))
    ):
        if "比" in text and re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE):
            return f"improve {metric} over the PatchCore baseline under the same evaluation protocol"
        if "提升" in text or "提高" in text or "优化" in text:
            return f"improve {metric} under the same evaluation protocol"
    return value


def _infer_evaluation_protocol(text: str) -> str | None:
    if any(token in text for token in (
        "不改测试",
        "不能改测试",
        "不改指标",
        "不能改指标",
        "不能作弊",
        "不改评价",
        "官方评价",
        "原始设置",
        "原设置",
        "保持 baseline",
    )):
        return "keep baseline/original evaluation protocol; no test split or metric changes"
    return None


def _infer_compute_environment(text: str, confirmed: dict[str, Any]) -> dict[str, Any]:
    env: dict[str, Any] = {}
    budget = confirmed.get("budget")
    if isinstance(budget, dict):
        env["budget"] = budget
    gpu_match = re.search(r"\b(h100|a100|l40s|rtx\s*4090|3090|4090)\b", text, re.IGNORECASE)
    if gpu_match:
        env["gpu"] = gpu_match.group(0)
    return env


def _infer_execution_mode(text: str) -> Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]:
    if any(token in text for token in ("每步审批", "每一步审批", "每步确认")):
        return "approve_each_step"
    if any(token in text for token in ("允许实验", "自动尝试", "后续 agents")):
        return "agent_assisted_after_approval"
    return "plan_only"


def _infer_improvement_hints(text: str, usable_evidence: list[Any] | None = None) -> list[str]:
    hints: list[str] = []
    lowered = text.lower()
    if any(token in lowered for token in ("feature adapter", "adapter", "适配层")) or "特征适配" in text:
        hints.append("feature_adapter")
    if any(token in lowered for token in ("dinov2", "backbone", "特征提取")):
        hints.append("feature_extractor")
    if "采样" in text or "coreset" in lowered:
        hints.append("sampling")
    if any(token in text for token in ("合成异常", "异常特征", "高斯噪声")):
        hints.append("synthetic_anomaly_features")
    if "判别器" in text or "discriminator" in lowered:
        hints.append("discriminator_score_calibration")
    if _accepts_paper_method_hints(text) and _evidence_mentions_simplenet(usable_evidence or []):
        hints.extend(["feature_adapter", "synthetic_anomaly_features", "discriminator_score_calibration"])
    return _merge_unique_list([], hints)


def _infer_target_module_hints(text: str) -> list[str]:
    hints: list[str] = []
    lowered = text.lower()
    if "backbone" in lowered or "特征提取" in text:
        hints.append("backbone_or_feature_extractor")
    if "memory bank" in lowered or "记忆库" in text:
        hints.append("memory_bank")
    if "后处理" in text or "postprocess" in lowered:
        hints.append("postprocess")
    return hints


def _infer_preferred_method_hints(text: str, usable_evidence: list[Any] | None = None) -> list[str]:
    hints: list[str] = []
    if "simplenet" in text.lower() or _evidence_mentions_simplenet(usable_evidence or []):
        hints.append("SimpleNet 论文方法")
    if "轻量" in text:
        hints.append("lightweight")
    if "蒸馏" in text or "distill" in text.lower():
        hints.append("distillation")
    if "注意力" in text or "attention" in text.lower():
        hints.append("attention")
    if "特征融合" in text or "feature fusion" in text.lower():
        hints.append("feature_fusion")
    return _merge_unique_list([], hints)


def _accepts_paper_method_hints(text: str) -> bool:
    return any(token in text for token in ("论文内", "论文里", "论文方法", "这些想法", "都可以尝试", "都列上"))


def _evidence_mentions_simplenet(usable_evidence: list[Any]) -> bool:
    for item in usable_evidence:
        if not isinstance(item, dict):
            continue
        haystack = " ".join(str(item.get(key) or "") for key in ("summary", "artifact_path", "evidence_type"))
        if "simplenet" in haystack.lower():
            return True
    return False


def _infer_risk_preference(text: str) -> str | None:
    if "保守" in text:
        return "conservative"
    if "激进" in text or "大胆" in text:
        return "aggressive"
    if "多方向" in text or "并行" in text:
        return "parallel_exploration"
    return None


def _confirmed_fields(contract: ResearchIntentContract) -> list[str]:
    if contract.need_spec.needs:
        fields = [
            need.name
            for need in contract.need_spec.needs
            if need.current_value not in (None, "", [], {})
        ]
        if contract.baseline_repo and "baseline_repo" not in fields:
            fields.append("baseline_repo")
        if contract.evaluation_protocol and "evaluation_protocol" not in fields:
            fields.append("evaluation_protocol")
        if contract.compute_environment and "compute_environment" not in fields:
            fields.append("compute_environment")
        return _merge_unique_list([], fields)

    fields: list[str] = []
    for field in CORE_REQUIRED_FIELDS:
        value = _required_field_value(contract, field)
        if value not in (None, "", [], {}):
            fields.append(field)
    if contract.baseline_repo:
        fields.append("baseline_repo")
    if contract.evaluation_protocol:
        fields.append("evaluation_protocol")
    if contract.compute_environment:
        fields.append("compute_environment")
    return fields


def _missing_core_fields(contract: ResearchIntentContract) -> list[str]:
    if contract.need_spec.needs:
        return list(contract.need_spec.blocking_needs)

    missing = []
    for field in CORE_REQUIRED_FIELDS:
        value = _required_field_value(contract, field)
        if value in (None, "", [], {}):
            missing.append(field)
    return missing


def _required_field_value(contract: ResearchIntentContract, field: str) -> Any:
    if field == "primary_metrics":
        return contract.primary_metrics
    return getattr(contract, field)


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _canonicalize_metric_list(metrics: list[str]) -> list[str]:
    return canonicalize_metrics(metrics)


def _unique_metrics(metrics: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for metric in metrics:
        if metric in seen:
            continue
        seen.add(metric)
        unique.append(metric)
    return unique


def _merge_unique_list(existing: Any, update: Any) -> list[Any]:
    values: list[Any] = []
    for item in (existing or []):
        if item not in values:
            values.append(item)
    for item in (update or []):
        if item not in values:
            values.append(item)
    return values


def _merge_evidence_sources(existing: list[dict[str, Any]], update: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *update]:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _merge_metric_intent(existing: MetricIntent, update: MetricIntent) -> MetricIntent:
    if update.extraction_source == "user_confirmed":
        return update.model_copy(deep=True)
    if update.primary_metrics:
        return update.model_copy(deep=True)
    return existing.model_copy(deep=True)


def _refresh_contract_state(contract: ResearchIntentContract) -> None:
    if not contract.primary_metrics and contract.primary_metric:
        contract.primary_metrics = [contract.primary_metric]
    if contract.primary_metrics:
        contract.primary_metric = contract.primary_metrics[0] if len(contract.primary_metrics) == 1 else None
    contract.metric_priority = contract.metric_priority or _metric_priority(contract.primary_metrics, contract.secondary_metrics)
    if not contract.metric_intent.primary_metrics and contract.primary_metrics:
        contract.metric_intent = MetricIntent(
            primary_metrics=contract.primary_metrics,
            secondary_metrics=contract.secondary_metrics,
            metric_priority=contract.metric_priority,
            extraction_source="fallback",
        )
    contract.need_spec = _sync_need_spec_from_contract(contract)
    contract.user_confirmed_fields = _confirmed_fields(contract)
    contract.missing_required_fields = _missing_core_fields(contract)
    contract.ready_for_plan = contract.need_spec.ready_for_plan if contract.need_spec.needs else not contract.missing_required_fields
    contract.ready_for_repo_analysis = contract.need_spec.ready_for_repo_analysis or bool(contract.baseline_repo)
    contract.ready_for_experiment_agents = bool(
        contract.ready_for_plan
        and contract.ready_for_repo_analysis
        and contract.baseline_entrypoint
        and contract.baseline_config
        and contract.evaluation_protocol
        and contract.compute_environment
    )


def _sync_need_spec_from_contract(contract: ResearchIntentContract) -> RequiredNeedSpec:
    spec = contract.need_spec.model_copy(deep=True)
    for need in spec.needs:
        value = _contract_value_for_need(contract, need.name)
        if value not in (None, "", [], {}):
            need.current_value = value
            if need.source == "unknown":
                need.source = "user"
            need.confidence = max(need.confidence, 0.9)
    return validate_need_spec(spec)


def _contract_value_for_need(contract: ResearchIntentContract, name: str) -> Any:
    mapping = {
        "research_goal": contract.research_goal,
        "baseline": contract.baseline,
        "dataset": contract.dataset,
        "metrics": contract.primary_metrics,
        "success_criteria": contract.success_criteria,
        "execution_mode": contract.execution_mode,
        "repo": contract.baseline_repo,
        "baseline_repo": contract.baseline_repo,
        "allowed_change_scope": contract.allowed_change_scope,
        "forbidden_change_scope": contract.forbidden_change_scope,
    }
    return mapping.get(name)
