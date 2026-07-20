"""Small, Attempt-scoped git worktree manager.

The manager deliberately owns only isolation and frozen hashes.  It does not
merge, promote, or otherwise alter the baseline/champion checkout.
"""

from __future__ import annotations

import subprocess
import shutil
import tempfile
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
        source_repository = repository_path.resolve()
        worktree = (self._root / attempt_id).resolve()
        if worktree == source_repository or source_repository.is_relative_to(worktree) or worktree.is_relative_to(source_repository):
            raise ValueError("Executor worktree must not overlap the source checkout")
        repository = self._baseline_repository(source_repository, attempt_id)
        self._require_clean(repository)
        resolved_base = self._git(repository, "rev-parse", "--verify", f"{base_commit}^{{commit}}")
        branch = f"executor/{attempt_id}"
        if worktree == repository or repository.is_relative_to(worktree) or worktree.is_relative_to(repository):
            raise ValueError("Executor worktree must not overlap the source checkout")
        if worktree.exists():
            existing_branch = self._git(worktree, "branch", "--show-current")
            if existing_branch != branch:
                raise ValueError("existing Executor worktree does not match the requested identity")
            return WorkspaceSpec(
                base_commit=resolved_base,
                worktree_path=str(worktree),
                branch=existing_branch,
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

    def _baseline_repository(self, source_repository: Path, attempt_id: str) -> Path:
        """Return a Git baseline without mutating a non-Git acquired source.

        Arbor uses a committed baseline before allocating isolated worktrees.
        Acquired archive repositories do not necessarily include Git metadata, so
        materialize that baseline under the run-owned executor area rather than
        initializing or changing the original acquired source directory.
        """

        if self._is_git_repository(source_repository):
            return self._repository_root(source_repository)

        snapshot_root = (self._root.parent / "executor_baselines" / attempt_id).resolve()
        snapshot_repository = snapshot_root / "repository"
        if snapshot_repository.is_dir():
            return self._repository_root(snapshot_repository)

        snapshot_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=f".{attempt_id}.", dir=snapshot_root.parent))
        try:
            temporary_repository = temporary_root / "repository"
            shutil.copytree(
                source_repository,
                temporary_repository,
                symlinks=False,
                ignore=shutil.ignore_patterns(".git"),
            )
            self._git(temporary_repository, "init", "-b", "baseline")
            self._git(temporary_repository, "config", "user.email", "autoad-snapshot@invalid")
            self._git(temporary_repository, "config", "user.name", "AutoAD snapshot")
            self._git(temporary_repository, "add", "--all")
            self._git(temporary_repository, "commit", "--no-gpg-sign", "-m", "AutoAD immutable baseline snapshot")
            if not snapshot_root.exists():
                temporary_root.replace(snapshot_root)
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

        return self._repository_root(snapshot_repository)

    @staticmethod
    def _is_git_repository(path: Path) -> bool:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"

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
