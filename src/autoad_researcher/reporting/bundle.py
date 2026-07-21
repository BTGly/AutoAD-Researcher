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
    "claim_evidence_map.json",
    "narrative_sections.json",
    "narrative_generation.json",
    "report_manifest.json",
    "report_snapshot.json",
    "delivery_state_snapshot.json",
    "bundle_exclusions.json",
)
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def run_bundle_job(run_dir: Path, job: dict[str, object]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report Package Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status != "content_ready":
        raise ValueError("report package requires content_ready")
    if state.format_status.html != "ready":
        raise ValueError("report package requires HTML readiness")
    directory = run_dir / "reports" / report_id
    if not (directory / "report.html").is_file():
        raise ValueError("report package requires HTML artifact")
    if state.format_status.bundle == "ready":
        return _outputs(run_dir, report_id)
    _write_delivery_snapshots(run_dir, report_id=report_id, job=job)
    names = [name for name in _BUNDLE_FILES if (directory / name).is_file()]
    required = {
        "report.md",
        "report.html",
        "report_facts.json",
        "evidence_index.json",
        "report_digest.json",
        "report_validation.json",
        "claim_evidence_map.json",
        "report_manifest.json",
        "report_snapshot.json",
        "delivery_state_snapshot.json",
        "bundle_exclusions.json",
    }
    if not required.issubset(names):
        raise ValueError("report package is missing required immutable artifacts")
    if any((directory / name).is_symlink() for name in names):
        raise ValueError("report package refuses symlink artifacts")
    checksums = "".join(f"{_sha256(directory / name)}  {name}\n" for name in names)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(names):
            _write_stable_entry(archive, name, (directory / name).read_bytes())
        _write_stable_entry(archive, "checksums.sha256", checksums.encode("utf-8"))
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


def _write_stable_entry(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    entry = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _write_delivery_snapshots(run_dir: Path, *, report_id: str, job: dict[str, object]) -> None:
    """Freeze delivery facts once; later State/PDF changes never alter this bundle."""

    store = ReportStore()
    manifest = store.load_manifest(run_dir, report_id)
    state = store.load_state(run_dir, report_id)
    package_job_id = job.get("job_id")
    packaged_at = job.get("created_at")
    if not isinstance(package_job_id, str) or not isinstance(packaged_at, str):
        raise ValueError("report Package Job lacks stable identity")
    delivery = {
        "schema_version": 1,
        "report_id": report_id,
        "snapshot_content_sha256": manifest.source_snapshot_content_sha256,
        "generation_status": state.generation_status,
        "format_status": state.format_status.model_dump(mode="json"),
        "artifact_refs": [item.model_dump(mode="json") for item in state.artifact_refs],
        "package_job_id": package_job_id,
        "packaged_at": packaged_at,
    }
    exclusions = {
        "schema_version": 1,
        "excluded": [
            {"path": "report.pdf", "reason": "v1 bundle keeps PDF as a separate optional download"},
            {"path": "report_state.json", "reason": "mutable delivery state is not part of an immutable bundle"},
            {"path": "runs/", "reason": "report bundle uses a fixed report-artifact allow-list"},
        ],
    }
    from autoad_researcher.reporting.persistence import write_immutable_report_json

    write_immutable_report_json(
        run_dir,
        report_id=report_id,
        filename="delivery_state_snapshot.json",
        artifact_type="report_delivery_snapshot",
        value=delivery,
    )
    write_immutable_report_json(
        run_dir,
        report_id=report_id,
        filename="bundle_exclusions.json",
        artifact_type="report_bundle_exclusions",
        value=exclusions,
    )
