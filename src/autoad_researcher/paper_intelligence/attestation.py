"""Source attestation for paper PDFs.

Validates that a paper PDF is safe, within workspace, and computes
its identity fingerprint (SHA256, size, page count, MIME type).
"""

import hashlib
from pathlib import Path
from datetime import datetime, timezone

from autoad_researcher.paper_intelligence.errors import PaperSourceError
from autoad_researcher.paper_intelligence.ids import validate_workspace_path


def compute_pdf_sha256(path: Path) -> str:
    """Compute the SHA256 hash of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def estimate_page_count(path: Path) -> int | None:
    """Estimate page count from PDF by counting /Type /Page entries.

    Returns None if unable to determine (e.g., non-PDF or corrupted).
    """
    try:
        content = path.read_bytes()
        # Count occurrences of "/Type /Page" (simplified heuristic)
        count = content.count(b"/Type /Page")
        # Also count "/Type/Page" (no space)
        count += content.count(b"/Type/Page")
        return count if count > 0 else None
    except (OSError, UnicodeDecodeError):
        return None


SOURCE_FAILURE_CODES = {
    "PAPER_SOURCE_NOT_FOUND": "Source file does not exist",
    "PAPER_SOURCE_OUTSIDE_WORKSPACE": "Source path escapes workspace",
    "PAPER_SOURCE_SYMLINK_FORBIDDEN": "Symlink sources are not allowed",
    "PAPER_SOURCE_NOT_PDF": "Source is not a PDF file",
    "PAPER_SOURCE_TOO_LARGE": "Source file exceeds size limit",
    "PAPER_SOURCE_EMPTY": "Source file is empty",
    "PAPER_SOURCE_HASH_FAILED": "Could not compute source hash",
}

DEFAULT_MAX_SOURCE_BYTES = 200 * 1024 * 1024  # 200 MiB


def attest_paper_source(
    source_path: str,
    original_filename: str,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> dict:
    """Validate and attest a paper PDF source.

    Returns a dict suitable for constructing PaperSource.
    Raises PaperSourceError with a failure code on validation failure.
    """
    try:
        validated = validate_workspace_path(source_path)
    except ValueError as e:
        raise PaperSourceError(f"PAPER_SOURCE_OUTSIDE_WORKSPACE: {e}") from e

    path = Path(validated)
    if not path.exists():
        raise PaperSourceError("PAPER_SOURCE_NOT_FOUND")
    if path.is_symlink():
        raise PaperSourceError("PAPER_SOURCE_SYMLINK_FORBIDDEN")

    resolved = path.resolve()
    cwd = Path.cwd().resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError:
        raise PaperSourceError("PAPER_SOURCE_OUTSIDE_WORKSPACE: symlink resolves outside workspace")

    if not path.suffix.lower() == ".pdf":
        raise PaperSourceError("PAPER_SOURCE_NOT_PDF")

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise PaperSourceError("PAPER_SOURCE_EMPTY")
    if size_bytes > max_bytes:
        raise PaperSourceError(
            f"PAPER_SOURCE_TOO_LARGE: {size_bytes} > {max_bytes}"
        )

    try:
        pdf_sha256 = compute_pdf_sha256(path)
    except Exception as e:
        raise PaperSourceError(f"PAPER_SOURCE_HASH_FAILED: {e}") from e

    page_count = estimate_page_count(path)

    return {
        "source_pdf_sha256": pdf_sha256,
        "size_bytes": size_bytes,
        "page_count": page_count,
        "original_filename_label": original_filename,
        "mime_type": "application/pdf",
        "created_at": datetime.now(timezone.utc),
    }
