"""ast_parse — validate Python syntax of changed files."""
import ast
import subprocess
from pathlib import Path


def ast_parse_step(*, file_path: Path) -> list[str]:
    """Validate that a Python file has valid syntax using ast.parse.

    Returns a list of error messages (empty = valid).
    """
    if not file_path.exists():
        return [f"File not found: {file_path}"]

    if file_path.suffix != ".py":
        return []

    try:
        source = file_path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(file_path))
        return []
    except SyntaxError as exc:
        return [f"Syntax error in {file_path}: {exc}"]


def ast_parse_bulk(*, paths: list[Path]) -> dict[str, list[str]]:
    """Run ast_parse on multiple files, returning a dict keyed by path."""
    return {str(p): ast_parse_step(file_path=p) for p in paths}


def compileall_check(*, root: Path) -> list[str]:
    """Run compileall on a directory tree as a bulk syntax check.

    Returns list of error messages (empty = all valid).
    """
    try:
        proc = subprocess.run(
            ["python", "-m", "compileall", "-q", str(root)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            stderr = proc.stderr or ""
            return [line for line in stderr.split("\n") if line.strip()]
        return []
    except subprocess.TimeoutExpired:
        return ["compileall timed out"]
    except Exception as exc:
        return [f"compileall failed: {exc}"]
