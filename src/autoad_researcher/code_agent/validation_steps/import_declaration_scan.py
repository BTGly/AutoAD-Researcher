"""import_declaration_scan — static import declaration scanner.

Verifies that Python import statements in modified files are syntactically
valid and follow project conventions. Runs as an optional non-blocking step.
"""

import ast
from pathlib import Path


class ImportScanIssue:
    """Single issue found during import declaration scanning."""

    def __init__(self, path: str, line: int, message: str) -> None:
        self.path = path
        self.line = line
        self.message = message


def scan_import_declarations(file_paths: list[Path]) -> list[ImportScanIssue]:
    """Scan Python files for import declaration issues.

    Checks:
      - File is valid Python syntax (can be parsed by ast.parse)
      - Imports reference stdlib or project modules (basic scope check)

    Returns list of issues found (empty = clean).
    """
    issues: list[ImportScanIssue] = []

    for fp in file_paths:
        if not fp.is_file() or fp.suffix != ".py":
            continue
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            issues.append(ImportScanIssue(
                path=str(fp), line=getattr(exc, "lineno", 0),
                message=f"syntax error: {exc.msg}",
            ))
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _check_import(alias.name, fp, node.lineno, issues)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    _check_import(node.module, fp, node.lineno, issues)

    return issues


def _check_import(
    module_name: str,
    fp: Path,
    lineno: int,
    issues: list[ImportScanIssue],
) -> None:
    """Check a single import for basic validity."""
    import builtins
    if module_name in ("os", "sys", "re", "json", "math", "datetime",
                        "hashlib", "pathlib", "typing", "collections",
                        "functools", "itertools", "copy", "enum"):
        return
    stdlib_check = _is_stdlib(module_name)
    if stdlib_check is None:
        issues.append(ImportScanIssue(
            path=str(fp), line=lineno,
            message=f"unresolvable import: {module_name}",
        ))


def _is_stdlib(name: str) -> bool | None:
    """Check if module name is in Python stdlib by trying to find it."""
    try:
        import importlib.util
        spec = importlib.util.find_spec(name)
        if spec is None:
            return None
        return True
    except (ValueError, ImportError):
        return None
