"""Explicit human Champion promotion over an already-confirmed candidate."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.promotion import CandidateRegistry, PromotionApproval, PromotionService


class PromotionInput(BaseModel):
    """A human decision; repository and merge target stay server-derived."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^candidate_[0-9]{6}$")
    approved_by: str = Field(min_length=1)


class PromotionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_ref: str
    champion_event: dict


class PromotionControlService:
    """Create durable human approval and invoke the existing journaled merge."""

    def __init__(self) -> None:
        self._registry = CandidateRegistry()
        self._promotions = PromotionService(registry=self._registry)

    def promote(self, run_dir: Path, *, value: PromotionInput) -> PromotionResult:
        candidate = self._registry.load_candidate(run_dir, value.candidate_id)
        suffix = value.candidate_id.rsplit("_", 1)[1]
        approval_id = f"approval_{suffix}"
        policy_ref = self._write_policy(run_dir, candidate_id=value.candidate_id, approval_id=approval_id)
        try:
            approval = self._registry.load_approval(run_dir, approval_id)
            if (
                approval.candidate_id != value.candidate_id
                or approval.mode != "human"
                or approval.decision != "approved"
                or approval.approved_by != value.approved_by
            ):
                raise ValueError("approval ID already exists for a different human decision")
        except FileNotFoundError:
            approval = PromotionApproval(
                approval_id=approval_id,
                candidate_id=value.candidate_id,
                mode="human",
                decision="approved",
                policy_snapshot_ref=policy_ref,
                approved_by=value.approved_by,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        approval_ref = self._registry.create_approval(run_dir, approval)
        repository = self._repository_for_candidate(run_dir, candidate.attempt_id)
        trunk_before = _git(repository, "rev-parse", "HEAD")
        event = self._promotions.promote_and_merge_candidate(
            run_dir,
            journal_id=f"promotion_{suffix}",
            candidate_id=value.candidate_id,
            approval_id=approval_id,
            expected_trunk_commit=trunk_before,
            current_trunk_commit=lambda: _git(repository, "rev-parse", "HEAD"),
            merge_candidate=lambda snapshot: self._merge(repository, snapshot.source_branch),
        )
        return PromotionResult(approval_ref=approval_ref, champion_event=event.model_dump(mode="json"))

    @staticmethod
    def _repository_for_candidate(run_dir: Path, attempt_id: str) -> Path:
        workspace_path = run_dir / "attempts" / attempt_id / "workspace.json"
        raw = json.loads(workspace_path.read_text(encoding="utf-8"))
        worktree = raw.get("worktree_path")
        if not isinstance(worktree, str):
            raise ValueError("candidate workspace evidence is invalid")
        root = Path(worktree).resolve()
        allowed = (run_dir / "executor_worktrees").resolve()
        if not root.is_relative_to(allowed):
            raise ValueError("candidate workspace is outside the run-owned executor area")
        # `--show-toplevel` names this candidate worktree, not the checkout that
        # owns the baseline/trunk branch.  Git's worktree list is the authority
        # for that relationship; its first entry is the primary checkout.
        lines = _git(root, "worktree", "list", "--porcelain").splitlines()
        first = next((line.removeprefix("worktree ") for line in lines if line.startswith("worktree ")), None)
        if first is None:
            raise ValueError("candidate Git worktree has no primary checkout")
        repository = Path(first).resolve()
        if not repository.is_relative_to(run_dir.resolve()):
            raise ValueError("candidate repository is outside the run")
        if repository == root:
            raise ValueError("candidate worktree cannot be its own promotion target")
        return repository

    @staticmethod
    def _merge(repository: Path, branch: str) -> str:
        _git(repository, "merge", "--no-ff", branch, "-m", f"AutoAD promote {branch}")
        return _git(repository, "rev-parse", "HEAD")

    @staticmethod
    def _write_policy(run_dir: Path, *, candidate_id: str, approval_id: str) -> str:
        path = run_dir / "experiments" / "champions" / "policies" / f"{approval_id}.json"
        payload = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "decision": "approved",
            "mode": "human",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("candidate_id") != candidate_id or existing.get("decision") != "approved" or existing.get("mode") != "human":
                raise ValueError("approval ID already exists for a different policy")
            return str(path.relative_to(run_dir))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(path.relative_to(run_dir))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, shell=False).stdout.strip()
