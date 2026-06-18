"""path_containment — ensure all touched paths are within approved scope."""
from pathlib import Path


def path_containment_step(
    *,
    touched_paths: set[str],
    approved_paths: set[str],
    policy_denied_paths: set[str],
) -> list[str]:
    """Verify every touched path is within the approved scope.

    Returns list of errors (empty = all paths contained).
    """
    errors: list[str] = []
    for path in touched_paths:
        if path in policy_denied_paths:
            errors.append(f"touched path {path} is policy-denied")
            continue
        for ancestor in _ancestors(path):
            if ancestor in policy_denied_paths:
                errors.append(f"ancestor {ancestor} of {path} is policy-denied")
                break
        else:
            if path not in approved_paths:
                if not _in_scope(path, approved_paths):
                    errors.append(f"touched path {path} not in approved scope")
    return errors


def resolve_containment(
    *,
    repository_root: Path,
    touched_paths: set[str],
) -> list[str]:
    """Verify no path escapes the repository root via symlink."""
    errors: list[str] = []
    for rel_path in touched_paths:
        full = (repository_root / rel_path).resolve()
        try:
            if not full.is_relative_to(repository_root.resolve()):
                errors.append(f"symlink escape detected: {rel_path} -> {full}")
        except AttributeError:
            if not str(full).startswith(str(repository_root.resolve()) + "/") and full != repository_root.resolve():
                errors.append(f"symlink escape detected: {rel_path} -> {full}")
    return errors


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    result = []
    while parts:
        result.append("/".join(parts))
        parts = parts[:-1]
    return result


def _in_scope(path: str, scope: set[str]) -> bool:
    if not scope:
        return False
    candidate = path
    while True:
        if candidate in scope:
            return True
        parts = candidate.split("/")
        if len(parts) <= 1:
            break
        candidate = "/".join(parts[:-1])
    return path in scope
