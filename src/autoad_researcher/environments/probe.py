"""Bounded, evidence-producing host and repository probes for environments."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import sha256_file


class ProbeCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    argv: list[str]
    exit_code: int | None
    status: Literal["success", "failed", "timeout", "unavailable"]
    stdout_path: str
    stderr_path: str


class HostProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    observed_at: str
    python_executable: str
    operating_system: dict[str, str] = Field(default_factory=dict)
    tools: dict[str, str | None] = Field(default_factory=dict)
    cuda_runtime: str | None = None
    gpu_available: bool = False
    gpu_capability: list[dict[str, str]] = Field(default_factory=list)
    torch: dict[str, Any] = Field(default_factory=dict)
    command_results: list[ProbeCommandResult] = Field(default_factory=list)


class RepositoryProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    observed_at: str
    repository_path: str
    repository_commit: str | None = None
    repository_dirty: bool = False
    repository_fingerprint: str
    dependency_files: list[str] = Field(default_factory=list)
    readme_files: list[str] = Field(default_factory=list)
    entrypoint_candidates: list[str] = Field(default_factory=list)
    declared_entrypoints: dict[str, str] = Field(default_factory=dict)
    existing_python_paths: list[str] = Field(default_factory=list)
    project_smoke_candidates: list[str] = Field(default_factory=list)
    command_results: list[ProbeCommandResult] = Field(default_factory=list)


ProbeRunner = Callable[[list[str], Path | None, int], subprocess.CompletedProcess[str]]

_SECRET_VALUE = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*)([^\s,;]+)"
)
_SECRET_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_HOST_PYTHON_PROBE = r"""
import json, platform, sys
print(json.dumps({
  "system": platform.system(), "release": platform.release(),
  "machine": platform.machine(), "python": platform.python_version(),
  "executable": sys.executable,
}, sort_keys=True))
"""
_TORCH_PROBE = r"""
import json
result = {"torch_present": False, "gpu_compute_ok": False}
try:
    import torch
    result.update({"torch_present": True, "torch_version": torch.__version__,
                   "cuda_available": bool(torch.cuda.is_available()),
                   "cuda_runtime": torch.version.cuda})
    if torch.cuda.is_available():
        try:
            value = torch.tensor([1.0], device="cuda")
            result["gpu_compute_ok"] = bool((value + value).item() == 2.0)
            result["gpu_name"] = torch.cuda.get_device_name(0)
            result["gpu_count"] = torch.cuda.device_count()
        except Exception as exc:
            result["gpu_compute_error"] = f"{type(exc).__name__}: {exc}"
except Exception as exc:
    result["torch_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result, sort_keys=True))
"""


def probe_host(
    output_dir: Path,
    *,
    python_executable: str | Path = sys.executable,
    runner: ProbeRunner | None = None,
) -> HostProbe:
    """Inspect executable tools and actual torch GPU computation with evidence."""
    commands: list[ProbeCommandResult] = []
    parsed: dict[str, dict[str, Any]] = {}
    command_specs = [
        ("host_python", [str(python_executable), "-c", _HOST_PYTHON_PROBE]),
        ("uv", ["uv", "--version"]),
        ("pip", [str(python_executable), "-m", "pip", "--version"]),
        ("conda", ["conda", "--version"]),
        (
            "nvidia_smi",
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,compute_cap,memory.total",
                "--format=csv,noheader,nounits",
            ],
        ),
        ("torch", [str(python_executable), "-c", _TORCH_PROBE]),
    ]
    for command_id, argv in command_specs:
        result, stdout = _run_probe_command(
            command_id,
            argv,
            output_dir,
            runner=runner,
        )
        commands.append(result)
        if result.status == "success" and command_id in {"host_python", "torch"}:
            parsed[command_id] = _parse_json(stdout)

    host = parsed.get("host_python", {})
    torch = parsed.get("torch", {})
    nvidia = _read_probe_stdout(output_dir, "nvidia_smi")
    gpu_capability = _parse_nvidia_smi(nvidia) if nvidia else []
    tools = {
        item.command_id: _tool_version(_read_probe_stdout(output_dir, item.command_id))
        if item.status == "success" else None
        for item in commands
        if item.command_id in {"uv", "pip", "conda"}
    }
    return HostProbe(
        observed_at=_utc_now(),
        python_executable=str(python_executable),
        operating_system={key: str(value) for key, value in host.items() if key != "executable"},
        tools=tools,
        cuda_runtime=_optional_string(torch.get("cuda_runtime")),
        gpu_available=bool(gpu_capability),
        gpu_capability=gpu_capability,
        torch=torch,
        command_results=commands,
    )


def probe_repository(
    repository_path: Path,
    output_dir: Path,
    *,
    source_id: str = "repository",
    runner: ProbeRunner | None = None,
) -> RepositoryProbe:
    """Read repository facts without executing project code."""
    root = repository_path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository_path must be a directory")
    # Import lazily: Repository Intelligence's public package also exposes the
    # existing EnvironmentPlan handoff, which imports this package.
    from autoad_researcher.repository_intelligence.structure_profile import (
        build_repository_structure_profile,
    )
    fingerprint = _repository_fingerprint(root)
    profile = build_repository_structure_profile(
        repository_root=root,
        source_id=source_id,
        source_fingerprint=fingerprint,
    )
    command_results: list[ProbeCommandResult] = []
    commit_result, commit = _run_probe_command(
        "repository_commit", ["git", "rev-parse", "HEAD"], output_dir, cwd=root, runner=runner,
    )
    dirty_result, dirty = _run_probe_command(
        "repository_dirty", ["git", "status", "--porcelain"], output_dir, cwd=root, runner=runner,
    )
    command_results.extend([commit_result, dirty_result])
    dependency_files = [
        path for path in profile.configuration_candidates
        if Path(path).name.lower() in {
            "pyproject.toml", "requirements.txt", "requirements-dev.txt", "environment.yml",
            "environment.yaml", "pipfile", "pipfile.lock", "poetry.lock", "setup.cfg", "setup.py", "uv.lock",
        }
        or Path(path).name.lower().startswith("requirements")
    ]
    readme_files = [
        path.relative_to(root).as_posix()
        for path in sorted(root.glob("README*"))
        if path.is_file() and not path.is_symlink()
    ]
    existing_python_paths = [
        candidate.relative_to(root).as_posix()
        for candidate in sorted(root.glob(".venv/bin/python"))
        if candidate.is_file()
    ]
    smoke_candidates = [
        path for path in profile.entrypoint_candidates
        if Path(path).suffix in {".py", ".sh"}
    ]
    return RepositoryProbe(
        observed_at=_utc_now(),
        repository_path=str(root),
        repository_commit=commit.strip() if commit_result.status == "success" else None,
        repository_dirty=bool(dirty.strip()) if dirty_result.status == "success" else False,
        repository_fingerprint=fingerprint,
        dependency_files=dependency_files,
        readme_files=readme_files,
        entrypoint_candidates=profile.entrypoint_candidates,
        declared_entrypoints=profile.declared_entrypoints,
        existing_python_paths=existing_python_paths,
        project_smoke_candidates=smoke_candidates,
        command_results=command_results,
    )


def write_probe(path: Path, probe: HostProbe | RepositoryProbe) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(probe.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_probe_command(
    command_id: str,
    argv: list[str],
    output_dir: Path,
    *,
    cwd: Path | None = None,
    timeout_seconds: int = 30,
    runner: ProbeRunner | None = None,
) -> tuple[ProbeCommandResult, str]:
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = (runner or _default_runner)(argv, cwd, timeout_seconds)
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        status: Literal["success", "failed", "timeout", "unavailable"] = (
            "success" if exit_code == 0 else "failed"
        )
    except FileNotFoundError as exc:
        exit_code, stdout, stderr, status = None, "", str(exc), "unavailable"
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        status = "timeout"
    stdout_path = logs_dir / f"{command_id}.stdout.log"
    stderr_path = logs_dir / f"{command_id}.stderr.log"
    stdout_path.write_text(_redact(stdout), encoding="utf-8")
    stderr_path.write_text(_redact(stderr), encoding="utf-8")
    return ProbeCommandResult(
        command_id=command_id,
        argv=argv,
        exit_code=exit_code,
        status=status,
        stdout_path=str(stdout_path.relative_to(output_dir)),
        stderr_path=str(stderr_path.relative_to(output_dir)),
    ), _redact(stdout)


def _default_runner(argv: list[str], cwd: Path | None, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, shell=False, check=False, capture_output=True,
        text=True, timeout=timeout_seconds,
    )


def _repository_fingerprint(root: Path) -> str:
    git_head = root / ".git" / "HEAD"
    if git_head.is_file():
        return sha256_file(git_head)
    entries = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    import hashlib
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _read_probe_stdout(output_dir: Path, command_id: str) -> str:
    path = output_dir / "logs" / f"{command_id}.stdout.log"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _parse_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_nvidia_smi(value: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for line in value.splitlines():
        values = [item.strip() for item in line.split(",")]
        if len(values) == 4 and all(values):
            result.append({"name": values[0], "driver_version": values[1], "compute_capability": values[2], "memory_mb": values[3]})
    return result


def _tool_version(value: str) -> str | None:
    return value.strip().splitlines()[0] if value.strip() else None


def _optional_string(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _redact(value: str) -> str:
    return _SECRET_TOKEN.sub("[REDACTED]", _SECRET_VALUE.sub(r"\1[REDACTED]", value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
