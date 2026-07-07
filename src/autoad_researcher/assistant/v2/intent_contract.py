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
    "primary_metric",
    "success_criteria",
    "execution_mode",
]


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
    primary_metric: str | None = None
    secondary_metrics: list[str] = Field(default_factory=list)
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

    ready_for_plan: bool = False
    ready_for_repo_analysis: bool = False
    ready_for_experiment_agents: bool = False


def build_contract_from_context(
    *,
    run_dir: Path,
    user_input: str,
    llm_context: dict[str, Any],
    transcript_tail: list[dict[str, Any]] | None = None,
) -> ResearchIntentContract:
    """Build a deterministic draft from confirmed chat facts and artifacts."""

    confirmed = dict(llm_context.get("confirmed_from_user") or {})
    combined_user_text = _combined_user_text(user_input, transcript_tail)
    sources = _load_source_registry_sources(run_dir)

    metrics = _listify(confirmed.get("metrics"))
    primary_metric = metrics[0] if metrics else _infer_primary_metric(combined_user_text)
    secondary_metrics = [metric for metric in metrics[1:] if metric != primary_metric]
    baseline_repo = _first_github_source(sources)

    contract = ResearchIntentContract(
        run_id=run_dir.name,
        task_domain=_infer_task_domain(combined_user_text),
        research_goal=_infer_research_goal(combined_user_text, confirmed),
        baseline=_clean_str(confirmed.get("baseline")) or _infer_baseline(combined_user_text),
        baseline_repo=baseline_repo,
        dataset=_clean_str(confirmed.get("dataset")) or _infer_dataset(combined_user_text),
        evaluation_protocol=_infer_evaluation_protocol(combined_user_text),
        primary_metric=primary_metric,
        secondary_metrics=secondary_metrics,
        success_criteria=_infer_success_criteria(combined_user_text, primary_metric),
        compute_environment=_infer_compute_environment(combined_user_text, confirmed),
        execution_mode=_infer_execution_mode(combined_user_text),
        user_improvement_hints=_infer_improvement_hints(combined_user_text),
        user_target_module_hints=_infer_target_module_hints(combined_user_text),
        preferred_method_hints=_infer_preferred_method_hints(combined_user_text),
        risk_preference=_infer_risk_preference(combined_user_text),
        evidence_sources=_contract_evidence_sources(sources, llm_context),
    )
    contract.user_confirmed_fields = _confirmed_fields(contract)
    contract.missing_required_fields = _missing_core_fields(contract)
    contract.ready_for_plan = not contract.missing_required_fields
    contract.ready_for_repo_analysis = bool(contract.baseline_repo)
    contract.ready_for_experiment_agents = bool(
        contract.ready_for_plan
        and contract.ready_for_repo_analysis
        and contract.baseline_entrypoint
        and contract.baseline_config
        and contract.evaluation_protocol
        and contract.compute_environment
    )
    return contract


def save_contract_draft(run_dir: Path, contract: ResearchIntentContract) -> Path:
    return _write_contract(run_dir / CONTRACT_DRAFT_FILE, contract)


def save_confirmed_contract(run_dir: Path, contract: ResearchIntentContract) -> Path:
    return _write_contract(run_dir / CONTRACT_FILE, contract)


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
        f"- 研究目标：{contract.research_goal or '待确认'}",
        f"- baseline：{contract.baseline or '待确认'}",
        f"- baseline repo：{contract.baseline_repo or '未提供，可后续由 repo analyzer 补'}",
        f"- dataset：{contract.dataset or '待确认'}",
        f"- primary metric：{contract.primary_metric or '待确认'}",
        f"- success criteria：{contract.success_criteria or '待确认'}",
        f"- execution mode：{contract.execution_mode}",
        f"- evaluation protocol：{contract.evaluation_protocol or '可后续由 repo/实验 agents 补全'}",
        f"- compute environment：{contract.compute_environment or '可后续由环境检测补全'}",
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
    if contract.missing_required_fields:
        lines.append("还缺少：" + ", ".join(contract.missing_required_fields))
        lines.append("你可以先回答最关键的一项：主要想优化指标、速度、显存、训练成本、复现跑通，还是稳定性/泛化？")
    else:
        lines.append("如果以上正确，请回复“确认”。确认后只写入 contract，不会自动 patch 或运行实验。")
    return "\n".join(lines)


def _write_contract(path: Path, contract: ResearchIntentContract) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contract.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


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
        return "提升 baseline 在目标数据集上的表现"
    if "复现" in text:
        return "复现并评估目标方法"
    return None


def _infer_baseline(text: str) -> str | None:
    if re.search(r"(patch\s*)?core|pathcore", text, re.IGNORECASE):
        return "PatchCore"
    return None


def _infer_dataset(text: str) -> str | None:
    if re.search(r"mvtec\s*(ad)?", text, re.IGNORECASE):
        return "MVTec AD"
    return None


def _infer_primary_metric(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"pixel.*(auc|auroc)|定位", lowered):
        return "pixel_level_auroc"
    if re.search(r"image.*(auc|auroc)|auroc|auc-?roc|auc\b", lowered):
        return "image_level_auroc"
    if "f1" in lowered:
        return "f1"
    if "accuracy" in lowered or "准确率" in text:
        return "accuracy"
    if "速度" in text or "推理" in text:
        return "inference_latency"
    if "显存" in text:
        return "peak_vram"
    return None


def _infer_success_criteria(text: str, primary_metric: str | None) -> str | None:
    if "复现跑通" in text or ("复现" in text and "跑通" in text):
        return "baseline or target method runs reproducibly"
    if "显存" in text:
        return "reduce peak VRAM without weakening the evaluation protocol"
    if "速度" in text or "推理" in text:
        return "reduce inference latency without weakening the evaluation protocol"
    if "提升" in text or "提高" in text or "优化" in text:
        metric = primary_metric or "primary metric"
        return f"improve {metric} under the same evaluation protocol"
    return None


def _infer_evaluation_protocol(text: str) -> str | None:
    if any(token in text for token in ("不改测试", "不改评价", "官方评价", "原始设置", "原设置", "保持 baseline")):
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


def _infer_improvement_hints(text: str) -> list[str]:
    hints: list[str] = []
    if any(token in text.lower() for token in ("feature adapter", "adapter", "适配层")):
        hints.append("feature_adapter")
    if any(token in text.lower() for token in ("dinov2", "backbone", "特征提取")):
        hints.append("feature_extractor")
    if "采样" in text or "coreset" in text.lower():
        hints.append("sampling")
    return hints


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


def _infer_preferred_method_hints(text: str) -> list[str]:
    hints: list[str] = []
    if "轻量" in text:
        hints.append("lightweight")
    if "蒸馏" in text or "distill" in text.lower():
        hints.append("distillation")
    if "注意力" in text or "attention" in text.lower():
        hints.append("attention")
    if "特征融合" in text or "feature fusion" in text.lower():
        hints.append("feature_fusion")
    return hints


def _infer_risk_preference(text: str) -> str | None:
    if "保守" in text:
        return "conservative"
    if "激进" in text or "大胆" in text:
        return "aggressive"
    if "多方向" in text or "并行" in text:
        return "parallel_exploration"
    return None


def _confirmed_fields(contract: ResearchIntentContract) -> list[str]:
    fields: list[str] = []
    for field in CORE_REQUIRED_FIELDS:
        value = getattr(contract, field)
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
    missing = []
    for field in CORE_REQUIRED_FIELDS:
        value = getattr(contract, field)
        if value in (None, "", [], {}):
            missing.append(field)
    return missing


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
