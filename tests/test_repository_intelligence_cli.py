"""Tests for Repository Intelligence R13 CLI."""

import json
import subprocess
from pathlib import Path

from autoad_researcher.cli import main


def make_repo(path: Path) -> None:
    path.mkdir()
    run(["git", "init", "-b", "main"], cwd=path)
    (path / "README.md").write_text("Demo\ntrain entrypoint\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    run(["git", "add", "README.md", "pyproject.toml"], cwd=path)
    run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"], cwd=path)


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)


def test_repository_intelligence_cli_local_success(tmp_path, capsys):
    repo = tmp_path / "repo"
    make_repo(repo)

    exit_code = main(
        [
            "repository-intelligence",
            "--run-id",
            "run_repo",
            "--runs-root",
            str(tmp_path / "runs"),
            "--local-path",
            str(repo),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    run_dir = tmp_path / "runs" / "run_repo"
    assert exit_code == 0
    assert payload["status"] == "success"
    assert (run_dir / "request.json").is_file()
    assert (run_dir / "repository_source.json").is_file()
    assert (run_dir / "analysis_progress.json").is_file()
    assert (run_dir / "repository_summary.json").is_file()
    assert (run_dir / "evidence_validation.json").is_file()
    assert (run_dir / "environment_plan_candidate.json").is_file()
    assert (run_dir / "clarification_question_candidates.json").is_file()
    assert (run_dir / "repository_intelligence_result.json").is_file()


def test_repository_intelligence_cli_resume_returns_existing_result(tmp_path, capsys):
    repo = tmp_path / "repo"
    make_repo(repo)
    args = [
        "repository-intelligence",
        "--run-id",
        "run_repo",
        "--runs-root",
        str(tmp_path / "runs"),
        "--local-path",
        str(repo),
        "--json",
    ]
    assert main(args) == 0
    capsys.readouterr()

    exit_code = main([*args, "--resume"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["message"] == "repository intelligence completed"


def test_repository_intelligence_cli_existing_run_without_resume_blocks(tmp_path, capsys):
    repo = tmp_path / "repo"
    make_repo(repo)
    args = [
        "repository-intelligence",
        "--run-id",
        "run_repo",
        "--runs-root",
        str(tmp_path / "runs"),
        "--local-path",
        str(repo),
    ]
    assert main(args) == 0
    capsys.readouterr()

    exit_code = main(args)

    assert exit_code == 3
    assert "run directory already exists" in capsys.readouterr().out
