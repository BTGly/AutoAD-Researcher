"""Repository preflight — verify a third-party benchmark repo matches expectations."""

import re
import subprocess
from pathlib import Path

from autoad_researcher.benchmarks.errors import BenchmarkPreflightError
from autoad_researcher.benchmarks.evidence import BenchmarkFileFingerprint, BenchmarkRepositoryState
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file


def collect_repository_state(*, case, repo_path: Path, workspace_root: Path) -> BenchmarkRepositoryState:
    _validate_repo_boundary(repo_path, workspace_root)
    repo_path = repo_path.resolve(strict=True)

    _ensure_git(repo_path)

    actual_commit = _git(repo_path, ["rev-parse", "HEAD"]).stdout.strip()
    if actual_commit != case.repository.commit_sha:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_HEAD_MISMATCH",
            message=f"expected {case.repository.commit_sha}")

    symref = subprocess.run(["git", "-C", str(repo_path), "symbolic-ref", "-q", "HEAD"],
        shell=False, check=False, capture_output=True, text=True, timeout=10)
    if symref.returncode == 0:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_NOT_DETACHED",
            message="HEAD must be detached")
    elif symref.returncode != 1:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_GIT_COMMAND_FAILED",
            message="git symbolic-ref failed")

    status = _git(repo_path, ["status", "--porcelain=v1", "--untracked-files=all"])
    if status.stdout.strip():
        raise BenchmarkPreflightError(check_name="repository", code="REPO_DIRTY",
            message="repository has uncommitted or untracked changes")

    remote_url = _normalize_url(_remote_url(repo_path))
    expected_url = _normalize_url(case.repository.url)
    if remote_url != expected_url:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_REMOTE_MISMATCH",
            message=f"expected origin matching {expected_url}")

    files = _collect_required_files(repo_path, case)
    fingerprint_data = {
        "schema_version": 1, "actual_commit": actual_commit,
        "detached_head": True, "dirty": False,
        "remote_url": remote_url, "required_files": [
            {"path": f.path, "size_bytes": f.size_bytes, "sha256": f.sha256} for f in files
        ],
    }
    return BenchmarkRepositoryState(
        schema_version=1, case_id=case.case_id,
        expected_commit=case.repository.commit_sha,
        actual_commit=actual_commit, detached_head=True, dirty=False,
        remote_url=remote_url, required_files=files,
        repository_fingerprint=canonical_sha256(fingerprint_data),
    )


def verify_repository_unchanged(*, before: BenchmarkRepositoryState, after: BenchmarkRepositoryState) -> None:
    if before.repository_fingerprint != after.repository_fingerprint:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_MUTATED",
            message="repository fingerprint changed after execution")


# --- helpers ---

def _validate_repo_boundary(repo_path: Path, workspace_root: Path) -> None:
    if repo_path.is_symlink():
        raise BenchmarkPreflightError(check_name="repository", code="REPO_SYMLINK_FORBIDDEN",
            message="repository root must not be a symlink")
    allowed = (workspace_root / "repos").resolve(strict=True)
    try:
        rp = repo_path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise BenchmarkPreflightError(check_name="repository", code="REPO_NOT_FOUND",
            message="repository path does not exist")
    try:
        rp.relative_to(allowed)
    except ValueError:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_PATH_OUTSIDE_WORKSPACE",
            message="repository must be inside workspace/repos")


def _ensure_git(repo_path: Path) -> None:
    result = subprocess.run(["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"],
        shell=False, check=False, capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise BenchmarkPreflightError(check_name="repository", code="REPO_NOT_GIT",
            message="not a git repository")


def _git(repo_path: Path, argv: list[str], *, allow_nonzero: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", "-C", str(repo_path)] + argv,
        shell=False, check=False, capture_output=True, text=True, timeout=10)
    if result.returncode != 0 and not allow_nonzero:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_GIT_COMMAND_FAILED",
            message=f"git {' '.join(argv)} failed")
    return result


def _remote_url(repo_path: Path) -> str:
    result = subprocess.run(["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        shell=False, check=False, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise BenchmarkPreflightError(check_name="repository", code="REPO_REMOTE_MISSING",
            message="no origin remote")
    return result.stdout.strip()


_CREDENTIALS_RE = re.compile(r"://[^@]+@")


def _normalize_url(url: str) -> str:
    url = url.removesuffix(".git")
    # Normalize SSH first, then check credentials on HTTP(S)
    if url.startswith("git@"):
        url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
    elif url.startswith("ssh://git@"):
        url = re.sub(r"^ssh://git@([^/]+)/", r"https://\1/", url)
    elif _CREDENTIALS_RE.search(url):
        raise BenchmarkPreflightError(check_name="repository", code="REPO_REMOTE_CONTAINS_CREDENTIALS",
            message="remote URL contains credentials")
    return url.removeprefix("https://")


def _collect_required_files(repo_path: Path, case) -> list[BenchmarkFileFingerprint]:
    paths: set[str] = set()
    r = case.repository
    if r.entrypoint_path:
        paths.add(r.entrypoint_path)
    if r.config_path:
        paths.add(r.config_path)
    for p in r.dependency_files:
        paths.add(p)
    for p in case.evaluation.evaluator_paths:
        paths.add(p)
    for p in case.evaluation.protected_paths:
        paths.add(p)

    files = []
    for rel in sorted(paths):
        fp = repo_path / rel
        try:
            resolved = fp.resolve(strict=True)
        except (FileNotFoundError, OSError):
            raise BenchmarkPreflightError(check_name="repository", code="REPO_REQUIRED_FILE_MISSING",
                message=f"required file missing: {rel}")
        if resolved.is_symlink() or fp.is_symlink():
            raise BenchmarkPreflightError(check_name="repository", code="REPO_REQUIRED_FILE_SYMLINK",
                message=f"required file must not be symlink: {rel}")
        try:
            resolved.relative_to(repo_path)
        except ValueError:
            raise BenchmarkPreflightError(check_name="repository", code="REPO_PATH_OUTSIDE_WORKSPACE",
                message=f"required file escapes repo: {rel}")
        # Check no parent dir is symlink
        for parent in fp.parents:
            if str(parent) == str(repo_path):
                break
            if parent.is_symlink():
                raise BenchmarkPreflightError(check_name="repository", code="REPO_REQUIRED_FILE_SYMLINK",
                    message=f"parent directory is symlink: {rel}")
        if not resolved.is_file():
            raise BenchmarkPreflightError(check_name="repository", code="REPO_REQUIRED_FILE_NOT_REGULAR",
                message=f"required file not a regular file: {rel}")
        size = resolved.stat().st_size
        if size == 0:
            raise BenchmarkPreflightError(check_name="repository", code="REPO_REQUIRED_FILE_EMPTY",
                message=f"required file is empty: {rel}")
        files.append(BenchmarkFileFingerprint(path=rel, size_bytes=size, sha256=sha256_file(resolved)))
    return files

