"""Narrow SEARCH/REPLACE editing with deterministic filesystem gates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.executor_contracts import InterventionContract, WorkspaceSpec, freeze_protected_hashes


class SearchReplaceEdit(BaseModel):
    """One aider-style edit, with the target supplied separately from content."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    search: str
    replace: str

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            raise ValueError("edit path must be worktree-relative")
        return value

    @property
    def signature(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class PatchGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    code: str
    detail: str


class PatchApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["applied", "unchanged", "rejected", "rolled_back"]
    strategy: str | None = None
    signature: str
    decision: PatchGateDecision
    diff_path: str | None = None


class PreApplyPatchGate:
    """Check only declared path, worktree, protected-hash, and non-empty bounds."""

    def __init__(self, *, contract: InterventionContract, workspace: WorkspaceSpec):
        self._contract = contract
        self._workspace = workspace
        self._root = Path(workspace.worktree_path).resolve()

    def check(self, edit: SearchReplaceEdit) -> PatchGateDecision:
        try:
            target = _resolve_under(self._root, edit.path)
        except ValueError as exc:
            return _deny("REPAIR_REJECTED_HARD", str(exc))
        if not target.is_file():
            return _deny("REPAIR_REJECTED_HARD", "edit target must be an existing file")
        if edit.path in self._workspace.protected_hashes:
            return _deny("REPAIR_REJECTED_HARD", "edit target is protected by frozen hash")
        if _covered_by(edit.path, self._contract.forbidden_paths):
            return _deny("REPAIR_REJECTED_HARD", "edit target is forbidden by InterventionContract")
        if not _covered_by(edit.path, self._contract.allowed_paths):
            return _deny("REPAIR_REJECTED_HARD", "edit target is outside InterventionContract.allowed_paths")
        if not edit.search or edit.search == edit.replace:
            return _deny("PATCH_EMPTY", "SEARCH/REPLACE must make a non-empty change")
        observed = freeze_protected_hashes(self._root, sorted(self._workspace.protected_hashes))
        if observed != self._workspace.protected_hashes:
            return _deny("REPAIR_REJECTED_HARD", "protected hashes changed before patch application")
        return PatchGateDecision(allowed=True, code="ALLOW", detail="deterministic pre-apply checks passed")


class PostApplyDiffGuard:
    """Verify the exact applied content and frozen protected hashes, or rollback."""

    def __init__(self, *, workspace: WorkspaceSpec):
        self._workspace = workspace
        self._root = Path(workspace.worktree_path).resolve()

    def check(self, *, expected_contents: dict[str, str], proposed_paths: set[str]) -> PatchGateDecision:
        try:
            changed_paths = set(_git(self._root, "diff", "--name-only").splitlines()) - {""}
        except subprocess.CalledProcessError:
            return _deny("ROLLBACK", "could not inspect worktree diff")
        if not changed_paths or not changed_paths <= proposed_paths:
            return _deny("ROLLBACK", f"actual diff contains paths outside the proposed patch: {sorted(changed_paths)}")
        for relative_path, expected in expected_contents.items():
            if _resolve_under(self._root, relative_path).read_text(encoding="utf-8") != expected:
                return _deny("ROLLBACK", "actual diff does not match the proposed replacement")
        observed = freeze_protected_hashes(self._root, sorted(self._workspace.protected_hashes))
        if observed != self._workspace.protected_hashes:
            return _deny("REPAIR_REJECTED_HARD", "protected hashes changed after patch application")
        return PatchGateDecision(allowed=True, code="ALLOW", detail="diff and protected hashes match proposal")


class SearchReplaceApplier:
    """Apply one unique edit using the three documented, non-fuzzy strategies."""

    def __init__(self, *, contract: InterventionContract, workspace: WorkspaceSpec):
        self._root = Path(workspace.worktree_path).resolve()
        self._pre = PreApplyPatchGate(contract=contract, workspace=workspace)
        self._post = PostApplyDiffGuard(workspace=workspace)
        self._applied_signatures: set[str] = set()

    def apply(self, edit: SearchReplaceEdit, *, diff_path: Path | None = None) -> PatchApplyResult:
        decision = self._pre.check(edit)
        if not decision.allowed:
            return PatchApplyResult(status="rejected", signature=edit.signature, decision=decision)
        path = _resolve_under(self._root, edit.path)
        original = path.read_text(encoding="utf-8")
        if edit.signature in self._applied_signatures or (edit.search not in original and edit.replace in original):
            return PatchApplyResult(status="unchanged", signature=edit.signature, decision=PatchGateDecision(allowed=True, code="ALREADY_APPLIED", detail="stable edit signature was already applied"))
        replaced, strategy = _replace_once(original, edit.search, edit.replace)
        if replaced is None:
            return PatchApplyResult(status="rejected", signature=edit.signature, decision=_deny("SEARCH_NOT_UNIQUE", "SEARCH did not have one unambiguous match"))
        _dirty_checkpoint(self._root)
        path.write_text(replaced, encoding="utf-8")
        syntax = _validate_python(path)
        if syntax is not None:
            _rollback(self._root)
            return PatchApplyResult(status="rolled_back", strategy=strategy, signature=edit.signature, decision=_deny("ROLLBACK", syntax))
        post = self._post.check(expected_contents={edit.path: replaced}, proposed_paths={edit.path})
        if not post.allowed:
            _rollback(self._root)
            return PatchApplyResult(status="rolled_back", strategy=strategy, signature=edit.signature, decision=post)
        self._applied_signatures.add(edit.signature)
        if diff_path is not None:
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_text(_git(self._root, "diff", "--", edit.path), encoding="utf-8")
        return PatchApplyResult(status="applied", strategy=strategy, signature=edit.signature, decision=post, diff_path=str(diff_path) if diff_path else None)


def parse_search_replace_blocks(text: str, *, path: str) -> list[SearchReplaceEdit]:
    """Parse the one-file aider block form without accepting free-form patches."""

    start = "<<<<<<< SEARCH\n"
    middle = "=======\n"
    end = ">>>>>>> REPLACE\n"
    if not text.startswith(start) or not text.endswith(end):
        raise ValueError("expected a complete <<<<<<< SEARCH / ======= / >>>>>>> REPLACE block")
    body = text[len(start) : -len(end)]
    if body.count(middle) != 1:
        raise ValueError("SEARCH/REPLACE block must contain exactly one separator")
    search, replace = body.split(middle, 1)
    return [SearchReplaceEdit(path=path, search=search, replace=replace)]


def _replace_once(original: str, search: str, replace: str) -> tuple[str | None, str | None]:
    exact_positions = _whole_line_positions(original, search)
    if len(exact_positions) == 1:
        position = exact_positions[0]
        return original[:position] + replace + original[position + len(search) :], "perfect_replace"
    whitespace = _missing_leading_whitespace_replace(original, search, replace)
    if whitespace is not None:
        return whitespace, "missing_leading_whitespace"
    dots = _dotdotdots_replace(original, search, replace)
    if dots is not None:
        return dots, "try_dotdotdots"
    return None, None


def _whole_line_positions(original: str, search: str) -> list[int]:
    """Find SEARCH only when it starts and ends on complete line boundaries."""

    positions: list[int] = []
    start = 0
    while True:
        position = original.find(search, start)
        if position < 0:
            return positions
        end = position + len(search)
        starts_line = position == 0 or original[position - 1] == "\n"
        ends_line = end == len(original) or search.endswith("\n") or original[end] == "\n"
        if starts_line and ends_line:
            positions.append(position)
        start = position + 1


def _missing_leading_whitespace_replace(original: str, search: str, replace: str) -> str | None:
    search_lines = search.splitlines(keepends=True)
    original_lines = original.splitlines(keepends=True)
    if not search_lines or len(search_lines) > len(original_lines):
        return None
    candidates: list[tuple[int, int]] = []
    for start in range(len(original_lines) - len(search_lines) + 1):
        offsets: list[int] = []
        for actual, wanted in zip(original_lines[start : start + len(search_lines)], search_lines, strict=True):
            if actual.lstrip(" \t") != wanted.lstrip(" \t"):
                break
            if wanted.strip(" \t\r\n"):
                offsets.append(_leading_width(actual) - _leading_width(wanted))
        else:
            if offsets and len(set(offsets)) == 1 and offsets[0] != 0:
                candidates.append((start, offsets[0]))
    if len(candidates) != 1:
        return None
    start, offset = candidates[0]
    replacement_lines = replace.splitlines(keepends=True)
    adjusted = "".join((" " * offset + line if line.strip(" \t\r\n") and offset > 0 else line) for line in replacement_lines)
    return "".join(original_lines[:start] + [adjusted] + original_lines[start + len(search_lines) :])


def _dotdotdots_replace(original: str, search: str, replace: str) -> str | None:
    marker = "...\n"
    if marker not in search:
        return None
    parts = search.split(marker)
    if len(parts) != 2 or not all(parts):
        return None
    before, after = parts
    if original.count(before) != 1 or original.count(after) != 1:
        return None
    begin = original.index(before)
    finish = original.index(after, begin + len(before)) + len(after)
    return original[:begin] + replace + original[finish:]


def _covered_by(path: str, policies: list[str]) -> bool:
    return any(path == policy or path.startswith(policy.rstrip("/") + "/") for policy in policies)


def _resolve_under(root: Path, relative_path: str) -> Path:
    path = PurePosixPath(relative_path)
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("edit path escapes the declared worktree")
    return resolved


def _leading_width(value: str) -> int:
    return len(value) - len(value.lstrip(" \t"))


def _validate_python(path: Path) -> str | None:
    if path.suffix != ".py":
        return None
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except (OSError, SyntaxError) as exc:
        return f"python syntax validation failed: {exc}"
    return None


def _dirty_checkpoint(root: Path) -> None:
    if not _git(root, "status", "--porcelain"):
        return
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=executor@autoad.invalid", "-c", "user.name=AutoAD Executor", "commit", "-m", "safety: pre-edit checkpoint")


def _rollback(root: Path) -> None:
    _git(root, "reset", "--hard", "HEAD")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, capture_output=True, shell=False).stdout.strip()


def _deny(code: str, detail: str) -> PatchGateDecision:
    return PatchGateDecision(allowed=False, code=code, detail=detail)
