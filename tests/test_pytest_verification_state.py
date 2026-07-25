import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "pytest_verification_state.py"


def _run_state(repo: Path, state_path: Path, action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), action, "--state-path", str(state_path)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def _init_repository(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)


def test_recorded_pytest_state_requires_an_identical_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repository(repo)
    state_path = repo / ".git" / "pytest-state.json"

    assert _run_state(repo, state_path, "matches").returncode == 1
    assert _run_state(repo, state_path, "record").returncode == 0
    assert _run_state(repo, state_path, "matches").returncode == 0

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pytest_command"] == "uv run --extra dev pytest -q --durations=20"
    assert state["worktree_fingerprint"]

    (repo / "tracked.py").write_text("value = 2\n", encoding="utf-8")
    assert _run_state(repo, state_path, "matches").returncode == 1


def test_untracked_files_invalidate_a_recorded_pytest_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repository(repo)
    state_path = repo / ".git" / "pytest-state.json"

    assert _run_state(repo, state_path, "record").returncode == 0
    (repo / "new_module.py").write_text("value = 3\n", encoding="utf-8")

    result = _run_state(repo, state_path, "matches")
    assert result.returncode == 1
    assert "worktree_fingerprint changed" in result.stdout
