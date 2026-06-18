"""before_after_identity — verify unchanged files remain identical."""
import hashlib
from pathlib import Path


def before_after_identity_step(
    *,
    repository_root: Path,
    unchanged_paths: set[str],
    before_fingerprints: dict[str, str],
) -> list[str]:
    """Verify that files not declared in change set have not been modified.

    For each path in unchanged_paths, compares the current SHA256 against
    its before_fingerprint.

    Returns list of errors (empty = identity verified).
    """
    errors: list[str] = []
    for path in unchanged_paths:
        file_path = repository_root / path
        if not file_path.exists():
            errors.append(f"unchanged file disappeared: {path}")
            continue
        try:
            current_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"cannot read unchanged file {path}: {exc}")
            continue

        expected = before_fingerprints.get(path)
        if expected is not None and current_sha != expected:
            errors.append(f"unchanged file modified: {path} "
                          f"(sha {current_sha[:16]} != expected {expected[:16]})")

    return errors
