"""Build a finite, checksum-verified portable report bundle."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from autoad_researcher.reporting.binary_persistence import write_immutable_report_bytes
from autoad_researcher.reporting.store import ReportStore

REPORT_BUNDLE_JOB_TYPE = "report_package"
_BUNDLE_FILES = (
    "report.md",
    "report.html",
    "report_facts.json",
    "evidence_index.json",
    "report_digest.json",
    "report_validation.json",
    "narrative_sections.json",
    "report_manifest.json",
)


def run_bundle_job(run_dir: Path, job: dict[str, object]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report Package Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status != "content_ready":
        raise ValueError("report package requires content_ready")
    if state.format_status.bundle == "ready":
        return _outputs(run_dir, report_id)
    directory = run_dir / "reports" / report_id
    names = [name for name in _BUNDLE_FILES if (directory / name).is_file()]
    required = {"report.md", "report_facts.json", "evidence_index.json", "report_digest.json", "report_validation.json", "report_manifest.json"}
    if not required.issubset(names):
        raise ValueError("report package is missing required immutable artifacts")
    checksums = "".join(f"{_sha256(directory / name)}  {name}\n" for name in names)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            archive.writestr(name, (directory / name).read_bytes())
        archive.writestr("checksums.sha256", checksums.encode("utf-8"))
    write_immutable_report_bytes(run_dir, report_id=report_id, filename="checksums.sha256", artifact_type="report_checksums", content=checksums.encode("utf-8"))
    write_immutable_report_bytes(run_dir, report_id=report_id, filename="report_bundle.zip", artifact_type="report_bundle", content=payload.getvalue())
    _verify_bundle(run_dir, report_id)
    store.set_format_status(run_dir, report_id=report_id, format_name="bundle", status="ready")
    return _outputs(run_dir, report_id)


def _verify_bundle(run_dir: Path, report_id: str) -> None:
    directory = run_dir / "reports" / report_id
    with zipfile.ZipFile(directory / "report_bundle.zip") as archive:
        lines = archive.read("checksums.sha256").decode("utf-8").splitlines()
        for line in lines:
            digest, name = line.split("  ", 1)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("report bundle checksum verification failed")


def _outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    return [str((directory / name).relative_to(run_dir)) for name in ("checksums.sha256", "report_bundle.zip")]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
