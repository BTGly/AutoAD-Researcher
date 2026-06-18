"""diff_integrity — verify proposed diff matches actual applied content."""
import difflib
from pathlib import Path
from typing import Optional


def diff_integrity_step(
    *,
    proposed_diff: str,
    repository_root: Path,
    changed_paths: list[str],
) -> list[str]:
    """Verify that the proposed unified diff accurately represents applied changes.

    For each changed file, generates a fresh diff between "before" (rollback blob
    or current content) and "after" (current content), then compares against the
    proposed diff segments for that file.

    Returns list of error messages (empty = integrity verified).
    """
    errors: list[str] = []
    for path in changed_paths:
        file_path = repository_root / path
        if not file_path.exists():
            errors.append(f"changed file not found for diff check: {path}")
            continue

        before_content = _extract_before_for_path(proposed_diff, path)
        after_content = file_path.read_text(encoding="utf-8")

        if before_content is None:
            before_content = ""

        regenerated = list(difflib.unified_diff(
            before_content.split("\n"),
            after_content.split("\n"),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        ))

        proposed_lines = _extract_diff_lines_for_path(proposed_diff, path)
        if proposed_lines is None:
            errors.append(f"no proposed diff found for {path}")
            continue

        if regenerated != proposed_lines:
            errors.append(f"diff integrity mismatch for {path}: "
                          f"regenerated {len(regenerated)} lines vs proposed {len(proposed_lines)}")

    return errors


def _extract_before_for_path(diff: str, path: str) -> Optional[str]:
    """Extract the 'before' content from a unified diff for a specific path."""
    lines = diff.split("\n")
    result: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("--- a/") and path in line:
            in_section = True
            continue
        if line.startswith("+++ b/"):
            if in_section:
                break
            continue
        if in_section:
            if line.startswith("-"):
                result.append(line[1:])
            elif line.startswith(" "):
                result.append(line[1:])
            elif line.startswith("@@ "):
                continue
            else:
                break
    return "\n".join(result) if result else None


def _extract_diff_lines_for_path(diff: str, path: str) -> Optional[list[str]]:
    """Extract unified diff lines for a specific path."""
    lines = diff.split("\n")
    result: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("--- a/") and path in line:
            in_section = True
            result.append(line)
            continue
        if in_section:
            result.append(line)
            if line.startswith("@@ ") and not line.startswith("@@ -0,0 +"):
                continue
    return result if result else None
