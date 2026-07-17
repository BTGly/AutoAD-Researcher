"""The sole, crash-recoverable writer of an Attempt's outcome card."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.failure_classifier import classify_or_load
from autoad_researcher.schemas.benchmark import BenchmarkEvaluationContract


class ProtectedArtifactHashes(BaseModel):
    """Frozen pre-run hashes for exactly one EvaluationContract's protected paths."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    hashes: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_hashes(self):
        for path, digest in self.hashes.items():
            relative_path = PurePosixPath(path)
            if relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
                raise ValueError("protected hash path must be run-relative")
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ValueError("protected hash must be a lowercase SHA-256 digest")
        return self


class ProtectedArtifactValidationReport(BaseModel):
    """Observed post-run comparison against a frozen protected-hash artifact."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    baseline_ref: str
    baseline_sha256: str
    status: Literal["passed", "changed", "invalid"]
    observed_hashes: dict[str, str | None]
    changed_paths: list[str]
    errors: list[str]


class OutcomeCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    attempt_id: str
    runtime_status: str
    attempt_category: str
    execution_result_ref: str
    health_events_ref: str | None = None
    failure_classification_ref: str | None = None
    metrics: dict[str, Any] | None = None
    evaluation_contract_ref: str | None = None
    protected_artifact_report_ref: str | None = None
    protected_artifact_validation_ref: str | None = None
    protocol_valid: bool = True
    protocol_errors: list[str] = []


def finalize_attempt(
    attempt_dir: Path,
    *,
    attempt_id: str,
    runtime_status: str,
    run_dir: Path | None = None,
    evaluation_contract_ref: str | None = None,
    evaluation_contract_sha256: str | None = None,
    protected_artifact_report_ref: str | None = None,
    protected_artifact_report_sha256: str | None = None,
) -> OutcomeCard:
    """Write one immutable card after all deterministic result checks have completed."""

    path = attempt_dir / "outcome_card.json"
    with _outcome_lock(attempt_dir):
        if path.is_file():
            return OutcomeCard.model_validate_json(path.read_text(encoding="utf-8"))
        failed = runtime_status != "COMPLETED"
        metrics = _metrics(attempt_dir / "metrics.json")
        if not failed and metrics is None:
            failed = True
        protocol_valid, protocol_errors, validation_ref = _validate_protocol_refs(
            run_dir,
            attempt_dir,
            evaluation_contract_ref,
            evaluation_contract_sha256,
            protected_artifact_report_ref,
            protected_artifact_report_sha256,
        )
        if not protocol_valid:
            failed = True
        classification_ref = None
        if failed:
            classify_or_load(attempt_dir)
            classification_ref = "failure_classification.json"
        card = OutcomeCard(
            attempt_id=attempt_id,
            runtime_status=runtime_status,
            attempt_category=(
                "protocol_violated"
                if not protocol_valid
                else "run_failed"
                if failed
                else "scientifically_evaluable"
            ),
            execution_result_ref="execution_result.json",
            health_events_ref="health_events.jsonl" if (attempt_dir / "health_events.jsonl").is_file() else None,
            failure_classification_ref=classification_ref,
            metrics=metrics,
            evaluation_contract_ref=evaluation_contract_ref,
            protected_artifact_report_ref=protected_artifact_report_ref,
            protected_artifact_validation_ref=validation_ref,
            protocol_valid=protocol_valid,
            protocol_errors=protocol_errors,
        )
        _write_json_atomic(path, card.model_dump(mode="json", exclude_none=True))
        return card


def _metrics(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _validate_protocol_refs(
    run_dir: Path | None,
    attempt_dir: Path,
    contract_ref: str | None,
    contract_sha: str | None,
    protected_ref: str | None,
    protected_sha: str | None,
) -> tuple[bool, list[str], str | None]:
    if run_dir is None:
        return (contract_ref is None and protected_ref is None, ["protocol refs require run_dir"] if contract_ref or protected_ref else [], None)
    if (contract_ref is None) != (contract_sha is None) or (protected_ref is None) != (protected_sha is None):
        return False, ["protocol ref and SHA-256 must be supplied together"], None
    if (contract_ref is None) != (protected_ref is None):
        return False, ["EvaluationContract and protected hashes must be supplied together"], None
    if contract_ref is None:
        return True, [], None
    contract_path = _resolve_ref(run_dir, contract_ref)
    protected_path = _resolve_ref(run_dir, protected_ref or "")
    if contract_path is None or not contract_path.is_file() or sha256_file(contract_path) != contract_sha:
        return False, ["EvaluationContract reference is missing, escapes run_dir, or changed"], None
    if protected_path is None or not protected_path.is_file() or sha256_file(protected_path) != protected_sha:
        return False, ["protected hash reference is missing, escapes run_dir, or changed"], None
    try:
        contract = BenchmarkEvaluationContract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        baseline = ProtectedArtifactHashes.model_validate_json(protected_path.read_text(encoding="utf-8"))
    except Exception:
        return False, ["EvaluationContract or protected hashes have invalid schema"], None
    required_paths = set(contract.protected_paths)
    if set(baseline.hashes) != required_paths:
        return False, ["protected hashes do not exactly cover EvaluationContract.protected_paths"], None
    observed: dict[str, str | None] = {}
    changed: list[str] = []
    for protected_relative_path, expected_hash in sorted(baseline.hashes.items()):
        artifact = _resolve_ref(run_dir, protected_relative_path)
        actual_hash = sha256_file(artifact) if artifact is not None and artifact.is_file() else None
        observed[protected_relative_path] = actual_hash
        if actual_hash != expected_hash:
            changed.append(protected_relative_path)
    report = ProtectedArtifactValidationReport(
        baseline_ref=protected_ref or "",
        baseline_sha256=protected_sha or "",
        status="passed" if not changed else "changed",
        observed_hashes=observed,
        changed_paths=changed,
        errors=[],
    )
    report_path = attempt_dir / "protected_artifact_validation.json"
    _write_json_atomic(report_path, report.model_dump(mode="json"))
    if changed:
        return False, [f"protected artifacts changed: {', '.join(changed)}"], report_path.name
    return True, [], report_path.name


def _resolve_ref(run_dir: Path, ref: str) -> Path | None:
    relative = PurePosixPath(ref)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        return None
    path = run_dir.joinpath(*relative.parts).resolve()
    return path if path.is_relative_to(run_dir.resolve()) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _outcome_lock(attempt_dir: Path, timeout: float = 5.0, stale_after_seconds: float = 30.0):
    path = attempt_dir / ".outcome_card.lock"
    deadline = time.monotonic() + timeout
    fd: int | None = None
    token = uuid.uuid4().hex
    while time.monotonic() < deadline:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            payload = {
                "pid": os.getpid(),
                "owner_token": token,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
            os.fsync(fd)
            break
        except FileExistsError:
            _recover_stale_lock(path, stale_after_seconds)
            time.sleep(0.02)
    if fd is None:
        raise TimeoutError("could not acquire outcome finalization lock")
    try:
        yield
    finally:
        os.close(fd)
        if _is_lock_owner(path, token):
            try:
                path.unlink()
            except OSError:
                pass


def _recover_stale_lock(path: Path, stale_after_seconds: float) -> None:
    try:
        age = time.time() - path.stat().st_mtime
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = payload.get("pid") if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return
    if age < stale_after_seconds or not isinstance(pid, int) or _pid_exists(pid):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _is_lock_owner(path: Path, token: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("owner_token") == token


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
