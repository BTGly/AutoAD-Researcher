"""Small, Attempt-scoped git worktree manager.

The manager deliberately owns only isolation and frozen hashes.  It does not
merge, promote, or otherwise alter the baseline/champion checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from autoad_researcher.experiment.executor_contracts import WorkspaceSpec, freeze_protected_hashes


class WorktreeManager:
    """Create reproducible, isolated worktrees outside the source checkout."""

    def __init__(self, worktrees_root: Path):
        self._root = worktrees_root.resolve()

    def create(
        self,
        *,
        repository_path: Path,
        attempt_id: str,
        base_commit: str,
        protected_paths: list[str],
        environment_snapshot_ref: str,
    ) -> WorkspaceSpec:
        repository = self._repository_root(repository_path)
        self._require_clean(repository)
        resolved_base = self._git(repository, "rev-parse", "--verify", f"{base_commit}^{{commit}}")
        branch = f"executor/{attempt_id}"
        worktree = (self._root / attempt_id).resolve()
        if worktree == repository or repository.is_relative_to(worktree) or worktree.is_relative_to(repository):
            raise ValueError("Executor worktree must not overlap the source checkout")
        if worktree.exists():
            existing = self.inspect(worktree)
            if existing.base_commit != resolved_base or existing.branch != branch:
                raise ValueError("existing Executor worktree does not match the requested identity")
            return WorkspaceSpec(
                base_commit=existing.base_commit,
                worktree_path=str(worktree),
                branch=existing.branch,
                protected_hashes=freeze_protected_hashes(worktree, protected_paths),
                environment_snapshot_ref=environment_snapshot_ref,
            )
        self._root.mkdir(parents=True, exist_ok=True)
        self._git(repository, "worktree", "add", "-b", branch, str(worktree), resolved_base)
        try:
            return WorkspaceSpec(
                base_commit=resolved_base,
                worktree_path=str(worktree),
                branch=branch,
                protected_hashes=freeze_protected_hashes(worktree, protected_paths),
                environment_snapshot_ref=environment_snapshot_ref,
            )
        except Exception:
            self.remove(worktree)
            raise

    def inspect(self, worktree_path: Path) -> WorkspaceSpec:
        worktree = worktree_path.resolve()
        if not worktree.is_relative_to(self._root):
            raise ValueError("worktree is outside the configured Executor root")
        base_commit = self._git(worktree, "rev-parse", "HEAD")
        branch = self._git(worktree, "branch", "--show-current")
        if not branch:
            raise ValueError("Executor worktree must have its own branch")
        # inspect cannot recover caller-owned protected/environment inputs.
        return WorkspaceSpec(
            base_commit=base_commit,
            worktree_path=str(worktree),
            branch=branch,
            protected_hashes={".git": self._git(worktree, "rev-parse", "HEAD")},
            environment_snapshot_ref="inspection-only",
        )

    def remove(self, worktree_path: Path) -> None:
        worktree = worktree_path.resolve()
        if not worktree.is_relative_to(self._root):
            raise ValueError("refusing to remove a worktree outside the configured Executor root")
        if not worktree.exists():
            return
        repository = self._repository_root(worktree)
        self._git(repository, "worktree", "remove", "--force", str(worktree))

    def _repository_root(self, path: Path) -> Path:
        root = Path(self._git(path.resolve(), "rev-parse", "--show-toplevel")).resolve()
        return root

    def _require_clean(self, repository: Path) -> None:
        if self._git(repository, "status", "--porcelain"):
            raise ValueError("baseline/champion source checkout must be clean before creating an Executor worktree")

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
        return completed.stdout.strip()
