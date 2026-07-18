"""Temporary, bounded ExecutorAgent runtime for one isolated worktree."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.experiment.executor_contracts import InterventionContract, WorkspaceSpec
from autoad_researcher.experiment.patch_protocol import PatchApplyResult, SearchReplaceApplier, SearchReplaceEdit
from autoad_researcher.experiment.executor_repair import RepairRecord, append_repair_record, classify_repair_failure


class ExecutorLimits(BaseModel):
    """Explicit bounds for a disposable ExecutorAgent invocation."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(gt=0)
    max_wall_seconds: int = Field(gt=0)
    max_model_calls: int = Field(ge=0)
    allowed_commands: list[str] = Field(default_factory=lambda: ["python", "python3"])


class ExecutorProposal(BaseModel):
    """Structured provider output; semantic uncertainty is reported, not classified."""

    model_config = ConfigDict(extra="forbid")

    edits: list[SearchReplaceEdit] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)
    possible_contract_deviation: str | None = None
    confidence: float = Field(ge=0, le=1)


class ExecutorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    status: Literal["completed", "implementation_failed", "budget_exhausted"]
    model_calls: int = Field(ge=0)
    steps: int = Field(ge=0)
    changed_files: list[str]
    changed_symbols: list[str]
    possible_contract_deviation: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    error: str | None = None


class ExecutorTools:
    """The only I/O operations an Executor invocation can use."""

    def __init__(self, *, worktree_path: Path, applier: SearchReplaceApplier, limits: ExecutorLimits, started_at: float | None = None):
        self._root = worktree_path.resolve()
        self._applier = applier
        self._limits = limits
        self._started_at = started_at if started_at is not None else time.monotonic()
        self.steps = 0

    def read_file(self, path: str) -> str:
        self._step()
        return self._path(path).read_text(encoding="utf-8")

    def search_files(self, query: str) -> list[str]:
        self._step()
        if not query:
            raise ValueError("search query must not be empty")
        matches: list[str] = []
        for candidate in sorted(self._root.rglob("*")):
            if candidate.is_file() and ".git" not in candidate.parts:
                try:
                    if query in candidate.read_text(encoding="utf-8"):
                        matches.append(str(candidate.relative_to(self._root)))
                except UnicodeDecodeError:
                    continue
        return matches

    def apply_edit(self, edit: SearchReplaceEdit, *, diff_path: Path | None = None) -> PatchApplyResult:
        self._step()
        return self._applier.apply(edit, diff_path=diff_path)

    def run_command(self, argv: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        self._step()
        if not argv:
            raise ValueError("Executor command argv must not be empty")
        executable = Path(argv[0]).name
        if executable not in self._limits.allowed_commands:
            raise PermissionError("Executor command is not in the configured allowlist")
        if timeout_seconds <= 0 or timeout_seconds > self._remaining_wall_seconds():
            raise TimeoutError("Executor command exceeds remaining wall-time budget")
        environment = {key: value for key, value in os.environ.items() if key not in {"http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"}}
        return subprocess.run(argv, cwd=self._root, env=environment, text=True, capture_output=True, timeout=timeout_seconds, shell=False, check=False)

    def _step(self) -> None:
        if self.steps >= self._limits.max_steps:
            raise RuntimeError("Executor step budget exhausted")
        if self._remaining_wall_seconds() <= 0:
            raise TimeoutError("Executor wall-time budget exhausted")
        self.steps += 1

    def _remaining_wall_seconds(self) -> int:
        return max(0, int(self._limits.max_wall_seconds - (time.monotonic() - self._started_at)))

    def _path(self, value: str) -> Path:
        relative = PurePosixPath(value)
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ValueError("Executor tool path must stay in its worktree")
        resolved = self._root.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError("Executor tool path escapes its worktree")
        return resolved


ProposalProvider = Callable[[ExecutorTools], ExecutorProposal | dict]


class ExecutorAgent:
    """Run one provider call and always leave an auditable executor_summary."""

    def __init__(self, *, contract: InterventionContract, workspace: WorkspaceSpec, artifact_dir: Path, limits: ExecutorLimits):
        self._contract = contract
        self._workspace = workspace
        self._artifact_dir = artifact_dir
        self._limits = limits

    def run(self, proposal_provider: ProposalProvider) -> ExecutorSummary:
        started_at = time.monotonic()
        applier = SearchReplaceApplier(contract=self._contract, workspace=self._workspace)
        tools = ExecutorTools(worktree_path=Path(self._workspace.worktree_path), applier=applier, limits=self._limits, started_at=started_at)
        summary: ExecutorSummary | None = None
        try:
            if self._limits.max_model_calls < 1:
                summary = ExecutorSummary(status="budget_exhausted", model_calls=0, steps=0, changed_files=[], changed_symbols=[], error="Executor model-call budget exhausted")
                return summary
            changed_files: list[str] = []
            hard_failures = 0
            proposal: ExecutorProposal | None = None
            for model_call in range(1, min(self._limits.max_model_calls, self._contract.max_repairs + 1) + 1):
                proposal = ExecutorProposal.model_validate(proposal_provider(tools))
                failed = None
                for edit in proposal.edits:
                    result = tools.apply_edit(edit, diff_path=self._artifact_dir / "patch.diff")
                    if result.status == "applied": changed_files.append(edit.path)
                    elif result.status in {"rejected", "rolled_back"}:
                        failed = result; break
                if failed is None:
                    summary = ExecutorSummary(status="completed", model_calls=model_call, steps=tools.steps, changed_files=sorted(set(changed_files)), changed_symbols=proposal.changed_symbols, possible_contract_deviation=proposal.possible_contract_deviation, confidence=proposal.confidence)
                    return summary
                classification = classify_repair_failure(failed.decision.code)
                if classification == "hard_policy_violation": hard_failures += 1
                repair_index = model_call
                if repair_index <= self._contract.max_repairs:
                    append_repair_record(self._artifact_dir / "repair_log.jsonl", RepairRecord(repair_index=repair_index, trigger=failed.decision.code, classification=classification, patch_ref="patch.diff", validation_result=failed.decision.detail))
                if hard_failures >= 2 or repair_index >= self._contract.max_repairs:
                    summary = ExecutorSummary(status="implementation_failed", model_calls=model_call, steps=tools.steps, changed_files=sorted(set(changed_files)), changed_symbols=proposal.changed_symbols, possible_contract_deviation=proposal.possible_contract_deviation, confidence=proposal.confidence, error=f"{failed.decision.code}: {failed.decision.detail}")
                    return summary
            assert proposal is not None
            summary = ExecutorSummary(status="implementation_failed", model_calls=min(self._limits.max_model_calls, self._contract.max_repairs + 1), steps=tools.steps, changed_files=sorted(set(changed_files)), changed_symbols=proposal.changed_symbols, possible_contract_deviation=proposal.possible_contract_deviation, confidence=proposal.confidence, error="repair budget exhausted")
            return summary
        except (RuntimeError, TimeoutError) as exc:
            summary = ExecutorSummary(status="budget_exhausted", model_calls=1 if summary is None else summary.model_calls, steps=tools.steps, changed_files=[], changed_symbols=[], error=str(exc))
            return summary
        except Exception as exc:
            summary = ExecutorSummary(status="implementation_failed", model_calls=1, steps=tools.steps, changed_files=[], changed_symbols=[], error=str(exc))
            return summary
        finally:
            if summary is None:
                summary = ExecutorSummary(status="implementation_failed", model_calls=0, steps=tools.steps, changed_files=[], changed_symbols=[], error="Executor terminated without a summary")
            self._write_summary(summary)

    def _write_summary(self, summary: ExecutorSummary) -> None:
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifact_dir / "executor_summary.json"
        path.write_text(json.dumps(summary.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ExecutorAgentFactory:
    """Use the established DeepAgents factory without granting broad tools."""

    def create(self, *, model, tools: ExecutorTools):
        from deepagents import create_deep_agent

        return create_deep_agent(
            model=model,
            tools=[tools.read_file, tools.search_files, tools.apply_edit, tools.run_command],
            system_prompt="You are a temporary AutoAD Executor. Work only in the supplied worktree. Use only supplied tools. Do not use network, Git, or arbitrary shell. Return an ExecutorProposal.",
            response_format=ExecutorProposal,
            checkpointer=False,
        )
