"""Durable, revisioned Idea Tree for one ExperimentSession."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.session_store import ExperimentSessionStore

IDEAS_DIR = "experiments/ideas"
IdeaNodeStatus = Literal[
    "DRAFT", "REVIEWED", "READY", "RUNNING", "SUPPORTED", "NOT_SUPPORTED",
    "INCONCLUSIVE", "PRUNED", "MERGED",
]
ExpectedCost = Literal["unknown", "low", "medium", "high"]


class IdeaInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    kind: Literal["observation", "reinterpretation", "propagated"]
    created_at: str


class IdeaNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str = Field(pattern=r"^idea_[0-9]{6}$")
    parent_id: str | None = Field(default=None, pattern=r"^idea_[0-9]{6}$")
    is_root: bool = False
    depth: int = Field(ge=0, le=3)
    mechanism: str | None = None
    hypothesis: str | None = None
    observable: str | None = None
    grounding: list[str] = Field(default_factory=list)
    expected_cost: ExpectedCost = "unknown"
    intervention_contract_ref: str | None = None
    status: IdeaNodeStatus = "DRAFT"
    attempt_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    cognitive_commit_refs: list[str] = Field(default_factory=list)
    insights: list[IdeaInsight] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.is_root:
            if self.parent_id is not None or self.depth != 0:
                raise ValueError("IdeaTree root must have no parent and depth=0")
        elif (
            self.parent_id is None
            or self.depth == 0
            or not all((self.mechanism, self.hypothesis, self.observable))
        ):
            raise ValueError("non-root IdeaNode needs parent, depth, mechanism, hypothesis, and observable")
        return self


class IdeaMutationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1)
    mutation: str
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_revision: int = Field(ge=1)


class IdeaTree(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    root_node_id: str = "idea_000000"
    nodes: list[IdeaNode] = Field(min_length=1)
    mutation_receipts: list[IdeaMutationReceipt] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def _validate_tree(self):
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("IdeaTree node IDs must be unique")
        by_id = {node.node_id: node for node in self.nodes}
        root = by_id.get(self.root_node_id)
        if root is None or not root.is_root:
            raise ValueError("IdeaTree requires its structural root node")
        for node in self.nodes:
            if node.is_root:
                continue
            parent = by_id.get(node.parent_id or "")
            if parent is None or node.depth != parent.depth + 1:
                raise ValueError("IdeaTree node parent/depth relationship is invalid")
            if node.node_id not in parent.children:
                raise ValueError("IdeaTree parent must list every child")
        return self

    def node(self, node_id: str) -> IdeaNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise ValueError("IdeaTree node not found")


class IdeaTreeStore:
    """All IdeaTree mutations are revision-checked and atomically persisted."""

    def create_or_get(self, run_dir: Path, *, session_id: str) -> tuple[IdeaTree, bool]:
        if ExperimentSessionStore().load(run_dir, session_id) is None:
            raise FileNotFoundError("experiment session not found")
        with self._lock(run_dir):
            path = self._path(run_dir, session_id)
            if path.is_file():
                return IdeaTree.model_validate_json(path.read_text(encoding="utf-8")), False
            now = _utc_now()
            tree = IdeaTree(
                run_id=run_dir.name,
                session_id=session_id,
                nodes=[IdeaNode(node_id="idea_000000", is_root=True, depth=0, status="REVIEWED", created_at=now, updated_at=now)],
                created_at=now,
                updated_at=now,
            )
            self._write_unlocked(path, tree)
            append_event(run_dir, "experiment.idea_tree.created", {"session_id": session_id, "tree_revision": 0})
            return tree, True

    def load(self, run_dir: Path, *, session_id: str) -> IdeaTree | None:
        path = self._path(run_dir, session_id)
        return IdeaTree.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    def add_node(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, parent_id: str, mechanism: str, hypothesis: str, observable: str, grounding: list[str], expected_cost: ExpectedCost) -> IdeaTree:
        payload = {"parent_id": parent_id, "mechanism": mechanism, "hypothesis": hypothesis, "observable": observable, "grounding": grounding, "expected_cost": expected_cost}
        def mutate(tree: IdeaTree) -> IdeaTree:
            parent = tree.node(parent_id)
            if parent.status in {"PRUNED", "MERGED"}:
                raise ValueError("cannot add a child below a pruned or merged IdeaNode")
            if parent.depth >= 3:
                raise ValueError("IdeaTree maximum depth is 3")
            if any(node.parent_id == parent_id and node.mechanism == mechanism and node.hypothesis == hypothesis for node in tree.nodes):
                raise ValueError("duplicate IdeaNode under the same parent")
            now = _utc_now()
            child_id = _next_node_id(tree)
            child = IdeaNode(node_id=child_id, parent_id=parent_id, depth=parent.depth + 1, mechanism=mechanism, hypothesis=hypothesis, observable=observable, grounding=grounding, expected_cost=expected_cost, created_at=now, updated_at=now)
            nodes = [node.model_copy(update={"children": [*node.children, child_id], "updated_at": now}) if node.node_id == parent_id else node for node in tree.nodes]
            return tree.model_copy(update={"nodes": [*nodes, child]})
        return self._mutate(run_dir, session_id=session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, mutation="add_node", payload=payload, mutate=mutate)

    def attach_attempt(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, node_id: str, attempt_ref: str) -> IdeaTree:
        return self._append_ref(run_dir, session_id, expected_revision, idempotency_key, "attach_attempt", node_id, attempt_ref, "attempt_refs")

    def append_evidence(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, node_id: str, evidence_ref: str) -> IdeaTree:
        return self._append_ref(run_dir, session_id, expected_revision, idempotency_key, "append_evidence", node_id, evidence_ref, "evidence_refs")

    def append_cognitive_commit(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, node_id: str, commit_ref: str) -> IdeaTree:
        return self._append_ref(run_dir, session_id, expected_revision, idempotency_key, "append_cognitive_commit", node_id, commit_ref, "cognitive_commit_refs")

    def append_reinterpretation(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, node_id: str, text: str, evidence_refs: list[str]) -> IdeaTree:
        payload = {"node_id": node_id, "text": text, "evidence_refs": evidence_refs}
        def mutate(tree: IdeaTree) -> IdeaTree:
            tree.node(node_id)
            insight = IdeaInsight(text=text, evidence_refs=evidence_refs, kind="reinterpretation", created_at=_utc_now())
            return _replace_node(tree, node_id, lambda node: node.model_copy(update={"insights": [*node.insights, insight], "updated_at": _utc_now()}))
        return self._mutate(run_dir, session_id=session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, mutation="append_reinterpretation", payload=payload, mutate=mutate)

    def mark_status(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, node_id: str, status: IdeaNodeStatus) -> IdeaTree:
        payload = {"node_id": node_id, "status": status}
        def mutate(tree: IdeaTree) -> IdeaTree:
            node = tree.node(node_id)
            if node.is_root or status not in _ALLOWED_TRANSITIONS[node.status]:
                raise ValueError("illegal IdeaNode status transition")
            return _replace_node(tree, node_id, lambda current: current.model_copy(update={"status": status, "updated_at": _utc_now()}))
        return self._mutate(run_dir, session_id=session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, mutation="mark_status", payload=payload, mutate=mutate)

    def request_prune(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, node_id: str, reason: str) -> IdeaTree:
        if not reason.strip():
            raise ValueError("prune reason is required")
        payload = {"node_id": node_id, "reason": reason}
        def mutate(tree: IdeaTree) -> IdeaTree:
            node = tree.node(node_id)
            if node.is_root or node.status in {"RUNNING", "MERGED"}:
                raise ValueError("IdeaTree root, running node, and merged node cannot be pruned")
            now = _utc_now()
            return _replace_node(tree, node_id, lambda current: current.model_copy(update={"status": "PRUNED", "insights": [*current.insights, IdeaInsight(text=reason, kind="observation", created_at=now)], "updated_at": now}))
        return self._mutate(run_dir, session_id=session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, mutation="request_prune", payload=payload, mutate=mutate)

    def _append_ref(self, run_dir: Path, session_id: str, expected_revision: int, idempotency_key: str, mutation: str, node_id: str, value: str, field: Literal["attempt_refs", "evidence_refs", "cognitive_commit_refs"]) -> IdeaTree:
        payload = {"node_id": node_id, field: value}
        def mutate(tree: IdeaTree) -> IdeaTree:
            node = tree.node(node_id)
            values = getattr(node, field)
            return tree if value in values else _replace_node(tree, node_id, lambda current: current.model_copy(update={field: [*getattr(current, field), value], "updated_at": _utc_now()}))
        return self._mutate(run_dir, session_id=session_id, expected_revision=expected_revision, idempotency_key=idempotency_key, mutation=mutation, payload=payload, mutate=mutate)

    def _mutate(self, run_dir: Path, *, session_id: str, expected_revision: int, idempotency_key: str, mutation: str, payload: dict[str, Any], mutate: Callable[[IdeaTree], IdeaTree]) -> IdeaTree:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        digest = canonical_sha256(payload)
        with self._lock(run_dir):
            path = self._path(run_dir, session_id)
            if not path.is_file():
                raise FileNotFoundError("IdeaTree not found")
            tree = IdeaTree.model_validate_json(path.read_text(encoding="utf-8"))
            receipt = next((item for item in tree.mutation_receipts if item.idempotency_key == idempotency_key), None)
            if receipt is not None:
                if receipt.mutation != mutation or receipt.payload_sha256 != digest:
                    raise ValueError("same idempotency key, different IdeaTree mutation")
                return tree
            if tree.revision != expected_revision:
                raise ValueError("IdeaTree revision conflict")
            changed = mutate(tree)
            now = _utc_now()
            updated = changed.model_copy(update={"revision": tree.revision + 1, "updated_at": now, "mutation_receipts": [*changed.mutation_receipts, IdeaMutationReceipt(idempotency_key=idempotency_key, mutation=mutation, payload_sha256=digest, applied_revision=tree.revision + 1)]})
            self._write_unlocked(path, updated)
        append_event(run_dir, "experiment.idea_tree.mutated", {"session_id": session_id, "mutation": mutation, "tree_revision": updated.revision})
        return updated

    @staticmethod
    def _path(run_dir: Path, session_id: str) -> Path:
        return run_dir / IDEAS_DIR / f"{session_id}.json"

    @staticmethod
    def _write_unlocked(path: Path, tree: IdeaTree) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(tree.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True); raise

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0):
        path = run_dir / IDEAS_DIR / ".ideas.lock"; path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout; fd: int | None = None
        while time.monotonic() < deadline:
            try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR); break
            except FileExistsError: time.sleep(0.05)
        if fd is None: raise TimeoutError("could not acquire IdeaTree lock")
        try: yield
        finally:
            os.close(fd)
            try: path.unlink()
            except OSError: pass


_ALLOWED_TRANSITIONS: dict[IdeaNodeStatus, set[IdeaNodeStatus]] = {
    "DRAFT": {"REVIEWED", "PRUNED"}, "REVIEWED": {"READY", "PRUNED"},
    "READY": {"RUNNING", "PRUNED"}, "RUNNING": {"SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"},
    "SUPPORTED": {"MERGED", "PRUNED"}, "NOT_SUPPORTED": {"PRUNED"},
    "INCONCLUSIVE": {"READY", "PRUNED"}, "PRUNED": set(), "MERGED": set(),
}

def _replace_node(tree: IdeaTree, node_id: str, update: Callable[[IdeaNode], IdeaNode]) -> IdeaTree:
    return tree.model_copy(update={"nodes": [update(node) if node.node_id == node_id else node for node in tree.nodes]})

def _next_node_id(tree: IdeaTree) -> str:
    return f"idea_{max((int(node.node_id.removeprefix('idea_')) for node in tree.nodes), default=0) + 1:06d}"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
