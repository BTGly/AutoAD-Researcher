"""Atomic report-version storage under one run without a second database."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from autoad_researcher.reporting.models import (
    FormatState,
    GenerationStatus,
    ReportManifest,
    ReportSnapshot,
    ReportState,
    ReviewStatus,
)
from autoad_researcher.reporting.snapshot import snapshot_content_sha256, snapshot_policy_hash, utc_now

REPORTS_DIR = "reports"
MANIFEST_FILE = "report_manifest.json"
STATE_FILE = "report_state.json"
SNAPSHOT_FILE = "report_snapshot.json"

_GENERATION_TRANSITIONS: dict[GenerationStatus, set[GenerationStatus]] = {
    "queued": {"building_snapshot", "assembling_facts", "failed"},
    "building_snapshot": {"assembling_facts", "failed"},
    "assembling_facts": {"generating_narrative", "failed"},
    "generating_narrative": {"validating", "failed"},
    "validating": {"content_ready", "failed"},
    "content_ready": set(),
    "failed": {"queued", "assembling_facts", "generating_narrative", "validating"},
}


class ReportStore:
    """Own report identity, state transitions and atomic files for a run."""

    def create_or_get(
        self,
        run_dir: Path,
        *,
        snapshot: ReportSnapshot,
        report_recipe_hash: str,
        previous_report_id: str | None = None,
        parent_report_id: str | None = None,
        source_proposal_id: str | None = None,
    ) -> tuple[ReportManifest, bool]:
        content_sha = snapshot_content_sha256(snapshot)
        with self._lock(run_dir):
            existing = self._find_by_snapshot_unlocked(
                run_dir,
                snapshot.session_id,
                content_sha,
                report_recipe_hash,
                previous_report_id=previous_report_id,
                parent_report_id=parent_report_id,
                source_proposal_id=source_proposal_id,
            )
            if existing is not None:
                return existing, False
            version = self._next_version_unlocked(run_dir, snapshot.session_id)
            report_id = f"report_v{version:03d}_{content_sha[:8]}_{report_recipe_hash[:8]}"
            now = utc_now()
            manifest = ReportManifest(
                run_id=run_dir.name,
                session_id=snapshot.session_id,
                report_id=report_id,
                version=version,
                source_snapshot_content_sha256=content_sha,
                snapshot_policy_hash=snapshot_policy_hash(),
                report_recipe_hash=report_recipe_hash,
                created_at=now,
                previous_report_id=previous_report_id,
                parent_report_id=parent_report_id,
                source_proposal_id=source_proposal_id,
            )
            state = ReportState(report_id=report_id, updated_at=now)
            directory = self._report_dir(run_dir, report_id)
            if directory.exists():
                raise ValueError("report directory identity collision")
            directory.mkdir(parents=True)
            try:
                self._write_json_unlocked(directory / SNAPSHOT_FILE, snapshot.model_dump(mode="json"))
                self._write_json_unlocked(directory / MANIFEST_FILE, manifest.model_dump(mode="json"))
                self._write_json_unlocked(directory / STATE_FILE, state.model_dump(mode="json"))
            except Exception:
                for path in directory.iterdir():
                    path.unlink(missing_ok=True)
                directory.rmdir()
                raise
            return manifest, True

    def load_manifest(self, run_dir: Path, report_id: str) -> ReportManifest:
        return ReportManifest.model_validate_json(self._read_required(self._report_dir(run_dir, report_id) / MANIFEST_FILE))

    def load_snapshot(self, run_dir: Path, report_id: str) -> ReportSnapshot:
        return ReportSnapshot.model_validate_json(self._read_required(self._report_dir(run_dir, report_id) / SNAPSHOT_FILE))

    def load_state(self, run_dir: Path, report_id: str) -> ReportState:
        return ReportState.model_validate_json(self._read_required(self._report_dir(run_dir, report_id) / STATE_FILE))

    def list_manifests(self, run_dir: Path, *, session_id: str | None = None) -> list[ReportManifest]:
        directory = run_dir / REPORTS_DIR
        if not directory.is_dir():
            return []
        manifests: list[ReportManifest] = []
        for path in sorted(directory.glob(f"report_*/{MANIFEST_FILE}")):
            manifest = ReportManifest.model_validate_json(path.read_text(encoding="utf-8"))
            if session_id is None or manifest.session_id == session_id:
                manifests.append(manifest)
        return sorted(manifests, key=lambda item: item.version)

    def transition_generation(self, run_dir: Path, *, report_id: str, target: GenerationStatus) -> ReportState:
        def mutate(state: ReportState) -> ReportState:
            if state.generation_status == target:
                return state
            if target not in _GENERATION_TRANSITIONS[state.generation_status]:
                raise ValueError(f"invalid report generation transition: {state.generation_status} -> {target}")
            update = {"generation_status": target, "last_error": None if target != "failed" else state.last_error}
            if state.generation_status == "failed":
                update["retry_count"] = state.retry_count + 1
            return state.model_copy(update=update)

        return self._update_state(run_dir, report_id, mutate)

    def mark_failed(self, run_dir: Path, *, report_id: str, error: str) -> ReportState:
        def mutate(state: ReportState) -> ReportState:
            if state.generation_status == "content_ready":
                return state
            return state.model_copy(update={"generation_status": "failed", "last_error": error[:500]})

        return self._update_state(run_dir, report_id, mutate)

    def set_review_status(self, run_dir: Path, *, report_id: str, status: ReviewStatus) -> ReportState:
        return self._update_state(run_dir, report_id, lambda state: state.model_copy(update={"review_status": status}))

    def set_format_status(self, run_dir: Path, *, report_id: str, format_name: str, status: FormatState) -> ReportState:
        if format_name not in {"markdown", "html", "pdf", "bundle"}:
            raise ValueError("unknown report format")
        return self._update_state(
            run_dir,
            report_id,
            lambda state: state.model_copy(update={"format_status": state.format_status.model_copy(update={format_name: status})}),
        )

    def record_job(self, run_dir: Path, *, report_id: str, job_id: str) -> ReportState:
        def mutate(state: ReportState) -> ReportState:
            if job_id in state.job_ids:
                return state
            return state.model_copy(update={"job_ids": [*state.job_ids, job_id]})

        return self._update_state(run_dir, report_id, mutate)

    def _update_state(self, run_dir: Path, report_id: str, mutate: Callable[[ReportState], ReportState]) -> ReportState:
        with self._lock(run_dir):
            directory = self._report_dir(run_dir, report_id)
            state = ReportState.model_validate_json(self._read_required(directory / STATE_FILE))
            updated = mutate(state)
            if updated == state:
                return state
            now = utc_now()
            updated = updated.model_copy(update={"updated_at": now, "revision": state.revision + 1})
            self._write_json_unlocked(directory / STATE_FILE, updated.model_dump(mode="json"))
            return updated

    @staticmethod
    def _read_required(path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError("report artifact not found")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _report_dir(run_dir: Path, report_id: str) -> Path:
        if not report_id or "\x00" in report_id or "/" in report_id or "\\" in report_id or report_id in {".", ".."}:
            raise ValueError("invalid report_id")
        return run_dir / REPORTS_DIR / report_id

    def _find_by_snapshot_unlocked(
        self,
        run_dir: Path,
        session_id: str,
        content_sha: str,
        report_recipe_hash: str,
        *,
        previous_report_id: str | None,
        parent_report_id: str | None,
        source_proposal_id: str | None,
    ) -> ReportManifest | None:
        for manifest in self.list_manifests(run_dir, session_id=session_id):
            if (
                manifest.source_snapshot_content_sha256 == content_sha
                and manifest.report_recipe_hash == report_recipe_hash
                and manifest.previous_report_id == previous_report_id
                and manifest.parent_report_id == parent_report_id
                and manifest.source_proposal_id == source_proposal_id
            ):
                return manifest
        return None

    def _next_version_unlocked(self, run_dir: Path, session_id: str) -> int:
        versions = [manifest.version for manifest in self.list_manifests(run_dir, session_id=session_id)]
        return max(versions, default=0) + 1

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0) -> Iterator[None]:
        lock_path = run_dir / REPORTS_DIR / ".reports.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        if fd is None:
            raise TimeoutError(f"Could not acquire report lock for {run_dir} within {timeout}s")
        try:
            yield
        finally:
            os.close(fd)
            try:
                os.unlink(lock_path)
            except OSError:
                pass

    @staticmethod
    def _write_json_unlocked(path: Path, value: object) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
