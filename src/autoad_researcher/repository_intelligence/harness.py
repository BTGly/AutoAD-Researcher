"""Repository Intelligence harness core and stage-control API."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.repository_intelligence.control_models import AnalysisControlSignal
from autoad_researcher.repository_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.repository_intelligence.model_routing import (
    ModelRoutePurpose,
    ModelRouter,
    ModelRoutingDecision,
    RepositoryModelConfig,
    append_model_routing_decision,
    load_model_config,
)
from autoad_researcher.repository_intelligence.models import RepositoryIntelligenceRequest
from autoad_researcher.repository_intelligence.skills import LoadedSkillRecord, RepositoryStage
from autoad_researcher.repository_intelligence.stage_loading import load_stage_skill
from autoad_researcher.tools import (
    LoadedToolRecord,
    PermissionEngine,
    ToolRegistry,
    ToolSpec,
    default_repository_permission_engine,
    filesystem_tool_spec,
    git_clone_tool_spec,
    load_stage_tool_specs,
    process_tool_spec,
    web_fetch_tool_spec,
    web_search_tool_spec,
)

REQUIRED_ANALYSIS_COVERAGE = {
    "repository_summary",
    "entrypoints",
    "dependencies",
    "configurations",
    "evaluation",
    "data_assets",
}


class StageEntryRecord(BaseModel):
    """Audit record for one harness stage entry."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    stage: RepositoryStage
    skill_loaded: bool
    loaded_skill: LoadedSkillRecord | None = None
    loaded_tools: list[LoadedToolRecord]
    model_routing: ModelRoutingDecision
    resume_fingerprint: str = Field(pattern=Sha256Pattern)


class AnalysisTransitionDecision(BaseModel):
    """Harness decision after an agent AnalysisControlSignal."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    decision: Literal["continue_reading", "synthesis_ready", "forced_synthesis", "blocked"]
    reason: str = Field(min_length=1)
    no_progress_cycles: int = Field(ge=0)
    remaining_analysis_tool_calls: int = Field(ge=0)


class RepositoryIntelligenceHarness:
    """Deterministic R4 harness core.

    This class wires mandatory Skill loading, deferred tool loading, model
    routing, analysis control-signal recording, and resume fingerprinting. It
    does not acquire repositories, run LLM calls, or provide a CLI.
    """

    def __init__(
        self,
        *,
        runs_root: Path,
        skills_root: Path,
        model_config_path: Path,
        tool_registry: ToolRegistry | None = None,
        permission_engine: PermissionEngine | None = None,
    ):
        self.runs_root = runs_root
        self.skills_root = skills_root
        self.model_config_path = model_config_path
        self.model_config = load_model_config(model_config_path)
        self.model_router = ModelRouter(self.model_config)
        self.tool_registry = tool_registry or default_repository_tool_registry()
        self.permission_engine = permission_engine or default_repository_permission_engine()

    def enter_stage(
        self,
        *,
        request: RepositoryIntelligenceRequest,
        stage: RepositoryStage,
        route_purpose: ModelRoutePurpose,
        loaded_at: str,
    ) -> StageEntryRecord:
        """Enter a stage, load mandatory Skill/tools, route model, and write audits."""
        run_dir = self.runs_root / request.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        skill_result = load_stage_skill(
            stage=stage,
            request=request,
            skills_root=self.skills_root,
            tool_registry=self.tool_registry,
            permission_engine=self.permission_engine,
            audit_path=None,
        )
        tool_load = load_stage_tool_specs(
            registry=self.tool_registry,
            stage=stage,
            trigger_reason="stage_entry",
            loaded_at=loaded_at,
        )
        routing = self.model_router.route(
            stage=stage,
            purpose=route_purpose,
            decision_id=f"model_route_{stage}",
        )
        append_model_routing_decision(run_dir / "model_routing_decisions.jsonl", routing)

        record = StageEntryRecord(
            schema_version=1,
            stage=stage,
            skill_loaded=skill_result.record is not None,
            loaded_skill=skill_result.record,
            loaded_tools=tool_load.audit_records,
            model_routing=routing,
            resume_fingerprint=resume_fingerprint(
                request=request,
                stage=stage,
                skill=skill_result.record,
                model_config=self.model_config,
            ),
        )
        _write_json_atomic(run_dir / f"stage_entry_{stage}.json", record)
        return record

    def record_analysis_control_signal(
        self,
        *,
        request: RepositoryIntelligenceRequest,
        signal: AnalysisControlSignal,
        no_progress_cycles: int,
        remaining_analysis_tool_calls: int,
    ) -> AnalysisTransitionDecision:
        """Append an AnalysisControlSignal and return Harness transition decision."""
        run_dir = self.runs_root / request.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(run_dir / "analysis_control_signals.jsonl", signal)

        decision = decide_analysis_transition(
            signal=signal,
            request=request,
            no_progress_cycles=no_progress_cycles,
            remaining_analysis_tool_calls=remaining_analysis_tool_calls,
        )
        _append_jsonl(run_dir / "analysis_transition_decisions.jsonl", decision)
        return decision


def decide_analysis_transition(
    *,
    signal: AnalysisControlSignal,
    request: RepositoryIntelligenceRequest,
    no_progress_cycles: int,
    remaining_analysis_tool_calls: int,
) -> AnalysisTransitionDecision:
    """Apply minimum coverage, budget, and no-progress rules to one signal."""
    if signal.decision == "blocked":
        return AnalysisTransitionDecision(
            schema_version=1,
            decision="blocked",
            reason="agent reported blockers",
            no_progress_cycles=no_progress_cycles,
            remaining_analysis_tool_calls=remaining_analysis_tool_calls,
        )

    budget = request.budget
    max_no_progress = budget.max_no_progress_cycles if budget is not None else 2
    if no_progress_cycles >= max_no_progress or remaining_analysis_tool_calls == 0:
        return AnalysisTransitionDecision(
            schema_version=1,
            decision="forced_synthesis",
            reason="analysis budget or no-progress limit reached",
            no_progress_cycles=no_progress_cycles,
            remaining_analysis_tool_calls=remaining_analysis_tool_calls,
        )

    if signal.decision == "synthesis_ready":
        missing = sorted(
            key
            for key in REQUIRED_ANALYSIS_COVERAGE
            if signal.coverage.get(key) not in {"confirmed", "checked_unknown", "conflicting"}
        )
        if missing:
            return AnalysisTransitionDecision(
                schema_version=1,
                decision="continue_reading",
                reason=f"synthesis_ready rejected; missing coverage: {missing}",
                no_progress_cycles=no_progress_cycles,
                remaining_analysis_tool_calls=remaining_analysis_tool_calls,
            )
        return AnalysisTransitionDecision(
            schema_version=1,
            decision="synthesis_ready",
            reason="minimum coverage satisfied",
            no_progress_cycles=no_progress_cycles,
            remaining_analysis_tool_calls=remaining_analysis_tool_calls,
        )

    return AnalysisTransitionDecision(
        schema_version=1,
        decision="continue_reading",
        reason="agent requested more repository reading",
        no_progress_cycles=no_progress_cycles,
        remaining_analysis_tool_calls=remaining_analysis_tool_calls,
    )


def default_repository_tool_registry() -> ToolRegistry:
    """Build the default Repository Intelligence tool registry."""
    registry = ToolRegistry()
    for name in ["filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat"]:
        registry = registry.register(filesystem_tool_spec(name))
    registry = registry.register(process_tool_spec())
    for spec in [
        web_search_tool_spec(),
        web_fetch_tool_spec(),
        git_clone_tool_spec(),
        _deferred_spec("github_read", "Read-only GitHub metadata, commit, and file reads.", "github"),
    ]:
        registry = registry.register(spec)
    return registry


def resume_fingerprint(
    *,
    request: RepositoryIntelligenceRequest,
    stage: RepositoryStage,
    skill: LoadedSkillRecord | None,
    model_config: RepositoryModelConfig,
) -> str:
    """Compute the R4 resume fingerprint fields available before acquisition."""
    payload = {
        "request_sha256": canonical_sha256(request),
        "stage": stage,
        "mandatory_skill_sha256": None if skill is None else skill.skill_sha256,
        "permission_profile": None if skill is None else skill.permission_profile,
        "model_config_sha256": model_config.config_sha256,
        "artifact_schema_version": 1,
        "budget_profile": request.budget_profile,
        "budget_sha256": None if request.budget is None else canonical_sha256(request.budget),
    }
    return canonical_sha256(payload)


def _deferred_spec(name: str, description: str, permission_category: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=True,
        destructive=False,
        concurrency_safe=True,
        deferred=True,
        permission_category=permission_category,
    )


def _write_json_atomic(path: Path, value: BaseModel) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True)
    with path.open("ab") as f:
        f.write(data.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())
