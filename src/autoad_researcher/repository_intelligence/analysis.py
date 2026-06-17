"""Skill-guided repository analysis primitives for Step 3.1 R7."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.repository_intelligence.auto_evidence import filesystem_read_with_evidence, filesystem_search_with_evidence
from autoad_researcher.repository_intelligence.control_models import AnalysisControlSignal
from autoad_researcher.repository_intelligence.evidence import ActiveRepositoryContext
from autoad_researcher.repository_intelligence.harness import AnalysisTransitionDecision, decide_analysis_transition
from autoad_researcher.repository_intelligence.ids import IdentifierPattern
from autoad_researcher.repository_intelligence.models import RepositoryAgentBudget, RepositoryIntelligenceRequest, RepositorySource
from autoad_researcher.repository_intelligence.status import ClaimStatus
from autoad_researcher.tools import (
    FilesystemReadRequest,
    FilesystemSearchRequest,
    PermissionEngine,
    ToolContext,
    default_repository_permission_engine,
)

CoverageStatus = Literal["confirmed", "checked_unknown", "conflicting", "not_checked"]
AnalysisStageStatus = Literal["running", "synthesis_ready", "blocked", "forced_complete"]
ObservationStatus = Literal["candidate", "confirmed", "conflicting", "discarded"]

DEPENDENCY_FILES = ["pyproject.toml", "requirements.txt", "requirements-dev.txt", "environment.yml", "setup.py"]
README_FILES = ["README.md", "README.rst", "readme.md"]
ENTRYPOINT_PATTERNS = ["train", "eval", "test", "infer"]


class AnalysisProgress(BaseModel):
    """Current analysis coverage, budget, blockers, and next actions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    iteration: int = Field(ge=1)
    stage_status: AnalysisStageStatus
    coverage: dict[str, CoverageStatus]
    evidence_count: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    file_reads_used: int = Field(ge=0)
    search_calls_used: int = Field(ge=0)
    llm_calls_used: int = Field(ge=0)
    input_tokens_used: int = Field(ge=0)
    new_evidence_count_last_cycle: int = Field(ge=0)
    no_progress_cycles: int = Field(ge=0)
    budget_exhausted: bool
    unresolved_blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class AnalysisObservation(BaseModel):
    """Brief evidence-backed repository observation. No hidden chain-of-thought."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    observation_id: str = Field(pattern=IdentifierPattern)
    category: str = Field(pattern=IdentifierPattern)
    summary: str = Field(min_length=1)
    status: ObservationStatus
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(min_length=1)


class RepositoryAnalysisCycleResult(BaseModel):
    """One deterministic repository analysis cycle."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    progress: AnalysisProgress
    observations: list[AnalysisObservation]
    control_signal: AnalysisControlSignal
    transition: AnalysisTransitionDecision


class RepositoryAnalysisAgent:
    """Small deterministic analysis agent skeleton for R7.

    The agent reads selected repository files through workspace-scoped
    filesystem tools and evidence middleware. It does not execute repository
    code, import repository modules, install dependencies, or run shell
    commands.
    """

    def __init__(self, *, permission_engine: PermissionEngine | None = None, max_read_bytes: int = 131072):
        self.permission_engine = permission_engine or default_repository_permission_engine()
        self.max_read_bytes = max_read_bytes

    def run_cycle(
        self,
        *,
        request: RepositoryIntelligenceRequest,
        source: RepositorySource,
        repository_root: Path,
        run_dir: Path,
        iteration: int,
        created_at: str,
        no_progress_cycles: int = 0,
    ) -> RepositoryAnalysisCycleResult:
        """Run one read-only analysis cycle and write progress artifacts."""
        if request.budget is None:
            budget = budget_for_profile(request.budget_profile)
            request = request.model_copy(update={"budget": budget})
        else:
            budget = request.budget

        run_dir.mkdir(parents=True, exist_ok=True)
        context = ActiveRepositoryContext(
            source_id=source.source_id,
            repository_root=repository_root,
            resolved_commit=source.resolved_commit,
            tree_sha=source.tree_sha,
        )
        tool_context = ToolContext(active_repository=context)
        observations: list[AnalysisObservation] = []
        evidence_ids: list[str] = []
        file_reads = 0
        search_calls = 0

        read_targets = _existing_first(repository_root, README_FILES)
        read_targets.extend(path for path in DEPENDENCY_FILES if (repository_root / path).is_file())
        for index, relative_path in enumerate(read_targets[: budget.max_analysis_file_reads], 1):
            result = filesystem_read_with_evidence(
                FilesystemReadRequest(
                    tool_call_id=f"tool_analysis_read_{index:03d}",
                    workspace_root=repository_root,
                    workspace_label=source.local_path_label,
                    path=relative_path,
                    max_bytes=self.max_read_bytes,
                    stage="analysis",
                    permission_profile="repository_analysis",
                    active_source_id=source.source_id,
                    tool_context=tool_context,
                ),
                permission_engine=self.permission_engine,
                evidence_index_path=run_dir / "evidence_index.jsonl",
                evidence_id=f"ev_analysis_read_{index:03d}",
            )
            if result.tool_result.status == "success":
                file_reads += 1
                evidence_ids.extend(ref.evidence_id for ref in result.evidence)
                observations.append(
                    AnalysisObservation(
                        observation_id=f"obs_read_{index:03d}",
                        category=_observation_category(relative_path),
                        summary=f"Read repository file {relative_path}",
                        status="confirmed",
                        evidence_ids=[ref.evidence_id for ref in result.evidence],
                        created_at=created_at,
                    )
                )

        search_path = read_targets[0] if read_targets else None
        for index, pattern in enumerate(ENTRYPOINT_PATTERNS[: budget.max_analysis_search_calls], 1):
            if search_path is None:
                break
            result = filesystem_search_with_evidence(
                FilesystemSearchRequest(
                    tool_call_id=f"tool_analysis_search_{index:03d}",
                    workspace_root=repository_root,
                    workspace_label=source.local_path_label,
                    path=search_path,
                    pattern=pattern,
                    max_matches=20,
                    stage="analysis",
                    permission_profile="repository_analysis",
                    active_source_id=source.source_id,
                    tool_context=tool_context,
                ),
                permission_engine=self.permission_engine,
                evidence_index_path=run_dir / "evidence_index.jsonl",
                evidence_id_prefix=f"ev_analysis_search_{index:03d}",
            )
            if result.tool_result.status == "success":
                search_calls += 1
                evidence_ids.extend(ref.evidence_id for ref in result.evidence)
                if result.evidence:
                    observations.append(
                        AnalysisObservation(
                            observation_id=f"obs_search_{index:03d}",
                            category="entrypoints",
                            summary=f"Found repository text matches for pattern {pattern!r}",
                            status="candidate",
                            evidence_ids=[ref.evidence_id for ref in result.evidence],
                            created_at=created_at,
                        )
                    )

        coverage = _coverage_from_observations(observations)
        budget_exhausted = file_reads >= budget.max_analysis_file_reads or search_calls >= budget.max_analysis_search_calls
        control_signal = AnalysisControlSignal(
            decision="synthesis_ready" if _minimum_cycle_coverage_met(coverage) else "continue_reading",
            coverage=coverage,
            new_evidence_count=len(evidence_ids),
            unresolved_blockers=[],
            next_actions=_next_actions(coverage),
        )
        transition = decide_analysis_transition(
            signal=control_signal,
            request=request,
            no_progress_cycles=no_progress_cycles,
            remaining_analysis_tool_calls=max(budget.max_analysis_tool_calls - file_reads - search_calls, 0),
        )
        stage_status: AnalysisStageStatus
        if transition.decision == "synthesis_ready":
            stage_status = "synthesis_ready"
        elif transition.decision == "forced_synthesis":
            stage_status = "forced_complete"
        elif transition.decision == "blocked":
            stage_status = "blocked"
        else:
            stage_status = "running"

        progress = AnalysisProgress(
            schema_version=1,
            iteration=iteration,
            stage_status=stage_status,
            coverage=coverage,
            evidence_count=len(evidence_ids),
            tool_calls_used=file_reads + search_calls,
            file_reads_used=file_reads,
            search_calls_used=search_calls,
            llm_calls_used=0,
            input_tokens_used=0,
            new_evidence_count_last_cycle=len(evidence_ids),
            no_progress_cycles=no_progress_cycles,
            budget_exhausted=budget_exhausted,
            unresolved_blockers=[],
            next_actions=control_signal.next_actions,
        )
        result = RepositoryAnalysisCycleResult(
            schema_version=1,
            progress=progress,
            observations=observations,
            control_signal=control_signal,
            transition=transition,
        )
        _write_json_atomic(run_dir / "analysis_progress.json", progress)
        for observation in observations:
            _append_jsonl(run_dir / "analysis_observations.jsonl", observation)
        _append_jsonl(run_dir / "analysis_control_signals.jsonl", control_signal)
        _append_jsonl(run_dir / "analysis_transition_decisions.jsonl", transition)
        return result


def budget_for_profile(profile: Literal["small", "medium", "large", "custom"]) -> RepositoryAgentBudget:
    """Return MVP budget baselines from the 3.1 plan."""
    if profile == "custom":
        raise ValueError("custom budget profile requires an explicit request budget")
    values = {
        "small": (50, 24, 12, 3, 80_000, 10_000, 8, 1),
        "medium": (90, 40, 22, 4, 140_000, 16_000, 12, 2),
        "large": (140, 60, 32, 6, 200_000, 24_000, 16, 2),
    }[profile]
    tool_calls, file_reads, search_calls, llm_calls, input_tokens, output_tokens, repair_tools, repair_llm = values
    return RepositoryAgentBudget(
        max_total_tool_calls=tool_calls + repair_tools,
        max_total_llm_calls=llm_calls + repair_llm,
        max_total_input_tokens=input_tokens,
        max_total_output_tokens=output_tokens,
        max_discovery_search_calls=0,
        max_discovery_fetch_calls=0,
        max_analysis_tool_calls=tool_calls,
        max_analysis_file_reads=file_reads,
        max_analysis_search_calls=search_calls,
        max_analysis_llm_calls=llm_calls,
        max_repair_tool_calls=repair_tools,
        max_repair_llm_calls=repair_llm,
        max_repairs=2,
        max_no_progress_cycles=2,
    )


def _existing_first(root: Path, names: list[str]) -> list[str]:
    return [name for name in names if (root / name).is_file()][:1]


def _observation_category(relative_path: str) -> str:
    lowered = relative_path.lower()
    if lowered.startswith("readme"):
        return "repository_summary"
    if lowered in {name.lower() for name in DEPENDENCY_FILES}:
        return "dependencies"
    return "repository_file"


def _coverage_from_observations(observations: list[AnalysisObservation]) -> dict[str, CoverageStatus]:
    coverage: dict[str, CoverageStatus] = {
        "repository_summary": "checked_unknown",
        "entrypoints": "checked_unknown",
        "dependencies": "checked_unknown",
        "configurations": "checked_unknown",
        "evaluation": "checked_unknown",
        "data_assets": "checked_unknown",
    }
    for observation in observations:
        if observation.category == "repository_summary":
            coverage["repository_summary"] = "confirmed"
        if observation.category == "dependencies":
            coverage["dependencies"] = "confirmed"
            coverage["configurations"] = "confirmed"
        if observation.category == "entrypoints":
            coverage["entrypoints"] = "confirmed"
    return coverage


def _minimum_cycle_coverage_met(coverage: dict[str, CoverageStatus]) -> bool:
    return coverage["repository_summary"] == "confirmed" and coverage["dependencies"] in {"confirmed", "checked_unknown"}


def _next_actions(coverage: dict[str, CoverageStatus]) -> list[str]:
    actions: list[str] = []
    if coverage["repository_summary"] != "confirmed":
        actions.append("read repository README or top-level documentation")
    if coverage["dependencies"] == "checked_unknown":
        actions.append("inspect dependency declaration files if present")
    if coverage["entrypoints"] != "confirmed":
        actions.append("search for train/eval/test/infer entrypoint candidates")
    return actions


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
