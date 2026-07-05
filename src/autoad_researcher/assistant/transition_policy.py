"""TransitionPolicy — 粗粒度 mode 迁移、不变量检查和 fallback。

不调用 LLM，不执行 pipeline。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autoad_researcher.assistant.events import AssistantEvent, AssistantEventType
from autoad_researcher.assistant.session import (
    AssistantMode,
    AutoADAssistantSession,
)


# ──────────────────────────────────────────────────────────────
# 允许的 mode 迁移
# ──────────────────────────────────────────────────────────────

_ALLOWED_TRANSITIONS: dict[AssistantMode, set[AssistantMode]] = {
    "goal_alignment":      {"material_alignment", "intent_structuring", "progress_reporting"},
    "material_alignment":  {"artifact_processing", "goal_alignment", "progress_reporting"},
    "artifact_processing": {"intent_structuring", "material_alignment", "pipeline_ready", "progress_reporting"},
    "intent_structuring":  {"task_confirmation", "goal_alignment", "material_alignment", "progress_reporting"},
    "task_confirmation":   {"intent_structuring", "pipeline_ready", "progress_reporting"},
    "pipeline_ready":      {"artifact_processing", "progress_reporting"},
    "progress_reporting":  {"goal_alignment", "material_alignment", "artifact_processing",
                            "intent_structuring", "task_confirmation", "pipeline_ready"},
}


@dataclass
class TransitionResult:
    """迁移结果。"""

    new_mode: AssistantMode
    allowed: bool = True
    reason: str = ""
    violations: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────────


def apply(
    session: AutoADAssistantSession,
    event: AssistantEvent,
) -> AutoADAssistantSession:
    """按 event 更新 session mode 并检查不变量。

    不调用 LLM。不执行 pipeline。
    返回更新后的 session（原 session 不修改）。
    """
    current_mode = session.mode
    new_mode = _resolve_mode(session, event)
    if not _transition_allowed(session, current_mode, new_mode):
        new_mode = current_mode
    session = session.model_copy(
        update={
            "mode": new_mode,
            "last_event_id": event.event_id,
        }
    )
    return session


def validate(session: AutoADAssistantSession) -> list[str]:
    """检查 session 是否违反不变量。返回违规列表。"""
    violations: list[str] = []

    # 1. 没有 confirmed task → 不能 ready_for_pipeline
    if session.task.ready_for_pipeline and session.task.confirmed_ref is None:
        violations.append("invariant_1: ready_for_pipeline requires confirmed task")

    # 2. 有 blocking gaps → 不能 ready_for_pipeline
    if session.task.ready_for_pipeline and session.task.has_blocking_gaps:
        violations.append("invariant_2: blocking gaps prevent ready_for_pipeline")

    # 3. ready_for_pipeline ≠ execution_approved
    if session.task.execution_approved and not session.task.ready_for_pipeline:
        violations.append("invariant_3: execution_approved requires ready_for_pipeline first")

    # 4. 来源一致性：parsed_ids / failed_ids 必须是 registered_ids 的子集
    registered = set(session.sources.registered_ids)
    parsed = set(session.sources.parsed_ids)
    failed = set(session.sources.failed_ids)
    if not parsed.issubset(registered):
        violations.append("invariant_4: parsed_ids must be subset of registered_ids")
    if not failed.issubset(registered):
        violations.append("invariant_4: failed_ids must be subset of registered_ids")

    return violations


# ──────────────────────────────────────────────────────────────
# mode 解析
# ──────────────────────────────────────────────────────────────


def _resolve_mode(session: AutoADAssistantSession, event: AssistantEvent) -> AssistantMode:
    current = session.mode
    event_type: AssistantEventType = event.event_type

    # ── user_input ──
    if event_type == "user_input":
        labels = set(event.router_labels)

        # 纠正 → 回到 intent_structuring
        if "correction" in labels:
            return "intent_structuring"

        # 确认 → task_confirmation
        if "confirmation" in labels:
            return "task_confirmation"

        # 拒绝或修改请求 → intent_structuring
        if "rejection" in labels or "revision_request" in labels:
            return "intent_structuring"

        # 目标/预算更新 → 回到 goal_alignment
        if "goal_update" in labels or "budget_constraint" in labels:
            return "goal_alignment"

        # 如果当前在 goal_alignment，自然语言输入保持
        if current == "goal_alignment":
            return "goal_alignment"

        # 默认保持在当前模式
        return current

    # ── source_input ──
    if event_type == "source_input":
        # 新材料 → 回到 material_alignment 或 artifact_processing
        if current in {"pipeline_ready", "task_confirmation"}:
            return "artifact_processing"
        return "material_alignment"

    # ── artifact_update ──
    if event_type == "artifact_update":
        # artifact 更新 → 如果当前在 material_alignment，进入 artifact_processing
        if current == "material_alignment":
            return "artifact_processing"
        return current

    # ── task_decision ──
    if event_type == "task_decision":
        labels = set(event.router_labels)
        if "revision_request" in labels or "rejection" in labels:
            return "intent_structuring"
        return "task_confirmation"

    # ── progress_query ──
    if event_type == "progress_query":
        return "progress_reporting"

    # ── system_update ──
    if event_type == "system_update":
        # 系统变化不影响用户交互模式
        return current

    # ── unknown ──
    if event_type == "unknown":
        # 保持当前模式，不崩溃
        return current

    return current

def _transition_allowed(
    session: AutoADAssistantSession,
    current: AssistantMode,
    new_mode: AssistantMode,
) -> bool:
    if current == new_mode:
        return True
    if new_mode in _ALLOWED_TRANSITIONS[current]:
        return True
    if (
        current == "goal_alignment"
        and new_mode == "task_confirmation"
        and (session.task.draft_ref is not None or session.interaction.pending_user_decision is not None)
    ):
        return True
    return False

