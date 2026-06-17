"""Benchmark environment lock contracts — spec, lockfile validation, and build evidence."""
import hashlib
from pathlib import Path, PurePosixPath
from typing import Literal

from packaging.requirements import Requirement
from pydantic import BaseModel, ConfigDict, Field, model_validator

Sha256Hex = r"^[0-9a-f]{64}$"
PythonVersion = r"^\d+\.\d+\.\d+$"


class PackageIndexSpec(BaseModel):
    """A package index used when resolving the lockfile."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://[^\s]+$")
    default: bool = False


def _validate_rel_path(value: str) -> str:
    if "\\" in value:
        raise ValueError(f"backslash forbidden in path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"absolute path forbidden: {value!r}")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"parent traversal forbidden: {value!r}")
    if value in {"", "."}:
        raise ValueError(f"file path required: {value!r}")
    return value


class BenchmarkEnvironmentSpec(BaseModel):
    """Environment contract. status=draft during planning, status=locked after lockfile generation."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: Literal["draft", "locked"]
    environment_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    case_id: str = Field(min_length=1)
    platform: Literal["linux_x86_64"]
    python_version: str | None = Field(default=None, pattern=PythonVersion)
    package_manager: Literal["uv"]
    package_manager_version: str | None = None
    package_indexes: list["PackageIndexSpec"] = Field(default_factory=list)
    requirements_input_path: str = Field(min_length=1)
    lockfile_path: str = Field(min_length=1)
    lockfile_sha256: str | None = Field(default=None, pattern=Sha256Hex)
    required_imports: list[str] = Field(min_length=1)
    accelerator: Literal["cuda"]
    gpu_index: int = Field(ge=0)
    allow_network_during_build: bool = True
    allow_network_during_execution: Literal[False]

    @model_validator(mode="after")
    def _validate_state(self):
        _validate_rel_path(self.requirements_input_path)
        _validate_rel_path(self.lockfile_path)
        if self.status == "draft":
            if self.python_version is not None or self.lockfile_sha256 is not None:
                raise ValueError("draft spec must not contain locked fields")
            return self
        if self.python_version is None:
            raise ValueError("locked spec requires python_version")
        if self.lockfile_sha256 is None:
            raise ValueError("locked spec requires lockfile_sha256")
        return self


class BenchmarkEnvironmentBuildEvidence(BaseModel):
    """Evidence from a successful build — all check flags must be True."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    environment_id: str
    case_id: str
    status: Literal["success"]
    python_version: str
    platform: str
    lockfile_sha256: str = Field(pattern=Sha256Hex)
    environment_sha256: str = Field(pattern=Sha256Hex)
    dependency_check_passed: Literal[True]
    import_probe_passed: Literal[True]
    cuda_probe_passed: Literal[True]
    patchcore_import_passed: Literal[True]
    allow_network_during_build: bool
    allow_network_during_execution: Literal[False]


def validate_lockfile(path: Path) -> list[str]:
    """Validate every dependency uses exact == pin. Rejects loose, URL, editable, local path, pending options."""
    if not path.is_file():
        raise ValueError(f"lockfile not found: {path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError("lockfile is empty")
    errors = []
    pinned_count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") or stripped.startswith("--"):
            errors.append(f"{stripped!r}: requirement options prohibited")
            continue
        try:
            req = Requirement(stripped)
        except Exception:
            errors.append(f"{stripped!r}: unparseable requirement")
            continue
        if req.url is not None:
            errors.append(f"{stripped!r}: direct URL dependency prohibited")
            continue
        specifiers = list(req.specifier)
        if len(specifiers) != 1:
            errors.append(f"{stripped!r}: must have exactly one version specifier")
            continue
        spec = specifiers[0]
        if spec.operator != "==" or "*" in spec.version:
            errors.append(f"{stripped!r}: only exact == pin allowed")
            continue
        pinned_count += 1
    if pinned_count == 0:
        errors.append("lockfile contains no pinned dependencies")
    return errors


def compute_lockfile_sha256(path: Path) -> str:
    return _sha256_file(path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()
