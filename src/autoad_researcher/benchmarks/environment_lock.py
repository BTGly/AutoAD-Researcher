"""Benchmark environment lock contracts — spec, lockfile validation, and build evidence."""
import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BenchmarkEnvironmentSpec(BaseModel):
    """Static environment contract. Lockfile is the single source of truth for deps."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    environment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    case_id: str
    platform: Literal["linux_x86_64"]
    python_version: str = Field(min_length=1)
    package_manager: Literal["uv"]
    requirements_input_path: str = Field(min_length=1)
    lockfile_path: str = Field(min_length=1)
    lockfile_sha256: str = Field(min_length=64, max_length=64)
    required_imports: list[str] = Field(min_length=1)
    accelerator: Literal["cuda"]
    gpu_index: int = Field(ge=0)
    allow_network_during_build: bool = True
    allow_network_during_execution: Literal[False]


class BenchmarkEnvironmentBuildEvidence(BaseModel):
    """Evidence from a successful environment build."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    environment_id: str
    case_id: str
    status: Literal["success"]
    python_version: str
    platform: str
    lockfile_sha256: str = Field(min_length=64, max_length=64)
    environment_sha256: str = Field(min_length=64, max_length=64)
    dependency_check_passed: bool
    import_probe_passed: bool
    cuda_probe_passed: bool
    patchcore_import_passed: bool
    allow_network_during_build: bool
    allow_network_during_execution: Literal[False]


_FORBIDDEN_DEP_PATTERNS = [
    (re.compile(r"[~*>]="), "loose constraint"),
    (re.compile(r"^git\+"), "git dependency"),
    (re.compile(r"^file://"), "local file dependency"),
    (re.compile(r"^\s*$"), "empty line"),
]


def validate_lockfile(path: Path) -> list[str]:
    """Validate lockfile: exists, non-empty, all deps precisely pinned, no prohibited patterns."""
    if not path.is_file():
        raise ValueError(f"lockfile not found: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError("lockfile is empty")
    errors = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pattern, msg in _FORBIDDEN_DEP_PATTERNS:
            if pattern.search(stripped):
                errors.append(f"{stripped!r}: {msg}")
    return errors


def compute_lockfile_sha256(path: Path) -> str:
    return _sha256_file(path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()
