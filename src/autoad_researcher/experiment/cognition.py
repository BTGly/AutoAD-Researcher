"""Append-only cognitive decisions and recoverable observation snapshots."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.benchmarks.hashing import canonical_sha256

COGNITION_DIR = "experiments/cognition"


class CognitiveCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    commit_id: str = Field(pattern=r"^commit_[0-9]{6}$")
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    tree_revision: int = Field(ge=0)
    input_outcome_refs: list[str] = Field(default_factory=list)
    observation: str = Field(min_length=1)
    comparison: str = Field(min_length=1)
    hypothesis_verdict: str = Field(min_length=1)
    keep_why: str = Field(min_length=1)
    failure_why: str = Field(min_length=1)
    mechanism_interpretation: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: str = Field(min_length=1)
    tree_mutations: list[str] = Field(default_factory=list)
    next_action: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    model_profile: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    created_at: str


class ObservationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    cycle_id: str = Field(min_length=1)
    tree_revision: int = Field(ge=0)
    outcome_refs: list[str] = Field(default_factory=list)
    observation: str = Field(min_length=1)
    ideation_focus: str = Field(min_length=1)
    created_at: str


class CoordinatorRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["resume_ideation", "reobserve"]
    reason: str
    snapshot: ObservationSnapshot | None = None


class CognitiveCommitStore:
    """A commit ledger never rewrites a previous scientific interpretation."""

    def append(self, run_dir: Path, *, session_id: str, idempotency_key: str, tree_revision: int, input_outcome_refs: list[str], observation: str, comparison: str, hypothesis_verdict: str, keep_why: str, failure_why: str, mechanism_interpretation: str, confidence: float, uncertainty: str, tree_mutations: list[str], next_action: str, evidence_refs: list[str], model_profile: str, prompt_version: str) -> tuple[CognitiveCommit, bool]:
        payload = {"tree_revision": tree_revision, "input_outcome_refs": input_outcome_refs, "observation": observation, "comparison": comparison, "hypothesis_verdict": hypothesis_verdict, "keep_why": keep_why, "failure_why": failure_why, "mechanism_interpretation": mechanism_interpretation, "confidence": confidence, "uncertainty": uncertainty, "tree_mutations": tree_mutations, "next_action": next_action, "evidence_refs": evidence_refs, "model_profile": model_profile, "prompt_version": prompt_version}
        digest = canonical_sha256(payload)
        with self._lock(run_dir, session_id):
            commits = self.load(run_dir, session_id=session_id)
            existing = next((commit for commit in commits if commit.idempotency_key == idempotency_key), None)
            if existing is not None:
                if canonical_sha256(_commit_identity(existing)) != digest:
                    raise ValueError("same idempotency key, different CognitiveCommit")
                return existing, False
            commit = CognitiveCommit(commit_id=f"commit_{len(commits) + 1:06d}", run_id=run_dir.name, session_id=session_id, idempotency_key=idempotency_key, created_at=_utc_now(), **payload)
            path = self._ledger_path(run_dir, session_id); path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(commit.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush(); os.fsync(handle.fileno())
        append_event(run_dir, "experiment.cognitive_commit.appended", {"session_id": session_id, "commit_id": commit.commit_id, "tree_revision": tree_revision})
        return commit, True

    def load(self, run_dir: Path, *, session_id: str) -> list[CognitiveCommit]:
        path = self._ledger_path(run_dir, session_id)
        if not path.is_file(): return []
        commits: list[CognitiveCommit] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip(): commits.append(CognitiveCommit.model_validate_json(line))
        return commits

    def write_observation_snapshot(self, run_dir: Path, *, session_id: str, snapshot: ObservationSnapshot) -> Path:
        path = self._snapshot_path(run_dir, session_id); path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True); raise
        append_event(run_dir, "experiment.observation_snapshot.written", {"session_id": session_id, "cycle_id": snapshot.cycle_id, "tree_revision": snapshot.tree_revision})
        return path

    def recovery(self, run_dir: Path, *, session_id: str, tree_revision: int) -> CoordinatorRecovery:
        path = self._snapshot_path(run_dir, session_id)
        if not path.is_file(): return CoordinatorRecovery(action="reobserve", reason="ObservationSnapshot is missing")
        snapshot = ObservationSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        if snapshot.tree_revision == tree_revision:
            return CoordinatorRecovery(action="resume_ideation", reason="ObservationSnapshot matches current IdeaTree revision", snapshot=snapshot)
        return CoordinatorRecovery(action="reobserve", reason="ObservationSnapshot tree revision is stale", snapshot=snapshot)

    @staticmethod
    def _ledger_path(run_dir: Path, session_id: str) -> Path: return run_dir / COGNITION_DIR / f"{session_id}.jsonl"
    @staticmethod
    def _snapshot_path(run_dir: Path, session_id: str) -> Path: return run_dir / COGNITION_DIR / session_id / "observation_snapshot.json"
    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, session_id: str, timeout: float = 5.0):
        path = run_dir / COGNITION_DIR / f".{session_id}.lock"; path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout; fd: int | None = None
        while time.monotonic() < deadline:
            try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR); break
            except FileExistsError: time.sleep(0.05)
        if fd is None: raise TimeoutError("could not acquire CognitiveCommit lock")
        try: yield
        finally:
            os.close(fd)
            try: path.unlink()
            except OSError: pass


def _commit_identity(commit: CognitiveCommit) -> dict[str, Any]:
    return {key: value for key, value in commit.model_dump(mode="json").items() if key not in {"schema_version", "commit_id", "run_id", "session_id", "idempotency_key", "created_at"}}

def _utc_now() -> str: return datetime.now(timezone.utc).isoformat()
