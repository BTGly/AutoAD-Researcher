"""Requirement discovery for V2 research conversations.

The resolver decides which facts block the next stage. Rules in this module
canonicalize values and validate readiness; they do not force a single metric
or require optional method-design hints.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

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


class ContractTurnRelevance(str, Enum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


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
        return validate_need_spec(canonicalize_need_values(RequiredNeedSpec.model_validate(llm_payload)))

    relevance = classify_contract_turn_relevance(user_input)
    if relevance is not ContractTurnRelevance.YES:
        return _non_contract_need_spec(current_stage_goal)

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
    return validate_need_spec(canonicalize_need_values(spec))


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
    relevance = classify_contract_turn_relevance(user_input)
    if relevance is ContractTurnRelevance.NO:
        return _non_contract_need_spec(current_stage_goal)
    if not api_key:
        if relevance is ContractTurnRelevance.UNKNOWN:
            return _non_contract_need_spec(current_stage_goal)
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

    from autoad_researcher.ui.chat_client import call_research_chat

    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model="deepseek-v4-flash",
        timeout_s=30,
    )
    payload = _parse_json_object(str(result.get("reply") or ""))
    if result.get("error") or payload is None:
        if relevance is ContractTurnRelevance.UNKNOWN:
            return _non_contract_need_spec(current_stage_goal)
        return discover_required_needs(**fallback_kwargs)
    try:
        spec = RequiredNeedSpec.model_validate(payload)
    except Exception:
        if relevance is ContractTurnRelevance.UNKNOWN:
            return _non_contract_need_spec(current_stage_goal)
        return discover_required_needs(**fallback_kwargs)
    spec.current_stage_goal = current_stage_goal
    return validate_need_spec(canonicalize_need_values(spec))


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
    system = (
        "你是 AutoAD Researcher 的 Need Discovery 组件，只输出 RequiredNeedSpec JSON。\n"
        "你的任务是判断当前目标和阶段真正缺哪些关键事实、材料、资源和安全约束。\n"
        "不要回答用户，不要输出 Markdown。\n"
        "用户不是每一句话都在填写研究合同。只有当前消息明确涉及研究目标、baseline、dataset、metric、success criteria、执行模式、资料、仓库、论文或实验边界时，才推进合同。\n"
        "身份问题、闲聊、玩笑、发泄、辱骂或无意义短句不是合同 turn：needs=[], blocking_needs=[], next_best_question=null，不要根据 existing_contract_draft.missing_required_fields 追问。\n"
        "existing_contract_draft 中的 missing_required_fields 只是状态，不代表每轮都要追问；只有当前 turn 正在推进研究任务时，才提出 next_best_question。\n"
        "规则边界：metric/dataset/baseline 名称可以标准化；不要用关键词或出现顺序强行决定用户意图。\n"
        "improvement_idea 和 target_module 只能是 optional，不能 blocking。\n"
        "plan 阶段不能要求用户提供 dataset_path、python_env、GPU、repo entrypoint 或 config；这些应是 required_later 或 auto_fillable。\n"
        "run_experiment 阶段必须检查 dataset_path、python_env、time_budget、human_review_policy。\n"
        "entrypoint/config 应由 repo analyzer 自动补，不能要求用户手写。\n"
        "每轮只给 next_best_question 一个最关键问题。\n"
        "JSON fields: task_summary, inferred_task_type, current_stage_goal, needs, blocking_needs, "
        "next_best_question, ready_for_plan, ready_for_repo_analysis, ready_for_experiment_design, "
        "ready_for_patch, ready_for_run. 每个 need 包含 name, category, required_for, necessity, "
        "current_value, source, confidence, blocking, question_to_user."
    )
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
        if not item_metrics and re.search(r"\bauroc\b|\bauc-?roc\b|\bauc\b", lowered):
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


def is_contract_relevant_turn(user_input: str) -> bool:
    """Compatibility wrapper for callers that only need a hard yes."""

    return classify_contract_turn_relevance(user_input) is ContractTurnRelevance.YES


def classify_contract_turn_relevance(user_input: str) -> ContractTurnRelevance:
    """Classify whether this turn should advance the research contract.

    YES means the message clearly advances research intent/material/experiment
    state. NO means it is clearly casual/identity/frustration. UNKNOWN should be
    judged by LLM Need Discovery when available instead of expanding regexes.
    """

    text = user_input.strip()
    if not text:
        return ContractTurnRelevance.NO
    lowered = text.lower()
    if re.search(r"https?://|github\.com|arxiv\.org", lowered):
        return ContractTurnRelevance.YES
    compact = re.sub(r"\s+", "", text)
    research_term_pattern = r"auroc|auc|f1\b|patchcore|efficientad|mvtec|visa\b|anomaly|detection|实验|指标|论文|仓库|数据集|异常|检测"
    if compact.startswith("你是") and re.search(research_term_pattern, lowered):
        return ContractTurnRelevance.UNKNOWN
    if compact in {"确认", "确认合同", "确认目标", "可以", "没问题", "同意", "就这样"}:
        return ContractTurnRelevance.YES
    non_contract_patterns = [
        r"^(你是谁|我是谁|我是谁\?|你是.+|我是人类!?|我是人类！?)$",
        r"^(我是傻逼|我草泥马|草泥马|操你|傻逼|无敌美少女)$",
        r"^(哈哈+|hhh+|hello|hi|你好|在吗|谢谢|ok|嗯|啊|哦)$",
    ]
    if any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in non_contract_patterns):
        return ContractTurnRelevance.NO
    explicit_research_patterns = [
        r"我想|我要|帮我|请|基于|主要看|成功标准|保持|不能改|不改|下载|解析|搜索|分析|总结|读取",
        r"提升|优化|改进|复现|训练|实验|评价|评估|运行|跑|报错|错误|traceback|exception|bug",
        r"baseline|dataset|metric|repo|repository|github|paper|arxiv|pdf|benchmark|config|entrypoint",
        r"指标|数据集|论文|仓库|资料|测试集|指标定义|泄漏|显存|速度|推理|方案|异常检测",
    ]
    if any(re.search(pattern, lowered) for pattern in explicit_research_patterns):
        return ContractTurnRelevance.YES
    if re.search(research_term_pattern, lowered):
        return ContractTurnRelevance.UNKNOWN
    contextual_patterns = [
        r"^(那就按.*来吧|就按.*|可以.*这个|继续|这个可以吗|按刚刚.*|就这个)$",
    ]
    if any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in contextual_patterns):
        return ContractTurnRelevance.UNKNOWN
    if len(compact) <= 12 and not any(char.isdigit() for char in compact):
        return ContractTurnRelevance.NO
    return ContractTurnRelevance.UNKNOWN


def _combined_user_text(user_input: str, transcript_tail: list[dict[str, Any]] | None) -> str:
    parts = [
        str(entry.get("content", ""))
        for entry in (transcript_tail or [])
        if entry.get("role") == "user"
    ]
    parts.append(user_input)
    return "\n".join(part for part in parts if part.strip())


def _non_contract_need_spec(current_stage_goal: StageGoal) -> RequiredNeedSpec:
    return RequiredNeedSpec(
        task_summary="non-contract chat turn",
        inferred_task_type="non_contract_chat",
        current_stage_goal=current_stage_goal,
        needs=[],
        blocking_needs=[],
        next_best_question=None,
        ready_for_plan=False,
        ready_for_repo_analysis=False,
        ready_for_experiment_design=False,
        ready_for_patch=False,
        ready_for_run=False,
    )


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
    if "成功标准" in text:
        return text
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
