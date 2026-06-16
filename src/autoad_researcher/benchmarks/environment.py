"""Benchmark environment preflight — probe isolated Python for deps, CUDA, GPU."""
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict

from autoad_researcher.benchmarks.errors import BenchmarkPreflightError
from autoad_researcher.benchmarks.evidence import BenchmarkEnvironmentSnapshot
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file

_PROBE_SOURCE = r"""
import json, sys, platform
result = {"python_version": platform.python_version(), "platform": f"{sys.platform}_{platform.machine()}"}
for pkg in ["torch", "torchvision"]:
    try:
        import importlib; m = importlib.import_module(pkg)
        result[f"{pkg}_version"] = m.__version__; result[f"{pkg}_present"] = True
    except ImportError:
        result[f"{pkg}_version"] = None; result[f"{pkg}_present"] = False
for pkg, attr in [("faiss", None), ("timm", None)]:
    try:
        import importlib; m = importlib.import_module(pkg)
        result[f"{pkg}_version"] = getattr(m, "__version__", "unknown") if attr is None else getattr(m, attr)
        result[f"{pkg}_present"] = True
    except ImportError:
        result[f"{pkg}_version"] = None; result[f"{pkg}_present"] = False
try:
    import torch as _t
    result["cuda_available"] = _t.cuda.is_available()
    result["cuda_device_count"] = _t.cuda.device_count() if result["cuda_available"] else 0
    if result["cuda_available"] and result["cuda_device_count"] > 0:
        props = _t.cuda.get_device_properties(0)
        result["cuda_runtime"] = _t.version.cuda or str(_t._C._cuda_getDriverVersion())
        result["gpu"] = {"index": 0, "name": props.name, "memory_mb": props.total_memory // (1024*1024)}
    else:
        result["cuda_available"] = False; result["cuda_device_count"] = 0
except Exception:
    result["cuda_available"] = False; result["cuda_device_count"] = 0
json.dump(result, sys.stdout)
"""


class _ProbeSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    python_version: str
    platform: str
    torch_version: str | None
    torch_present: bool
    torchvision_version: str | None
    torchvision_present: bool
    faiss_version: str | None
    faiss_present: bool
    timm_version: str | None
    timm_present: bool
    cuda_available: bool
    cuda_device_count: int
    cuda_runtime: str | None = None
    gpu: dict | None = None


def collect_environment_snapshot(
    *, case, benchmark_python: Path, lockfile_path: Path,
    workspace_root: Path, timeout_seconds: int = 30,
    probe_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> BenchmarkEnvironmentSnapshot:
    # Python boundary: launcher path must be in workspace/envs
    import os as _os
    launcher = Path(_os.path.abspath(str(benchmark_python)))
    envs_root = (workspace_root / "envs").resolve(strict=True)
    try:
        launcher.relative_to(envs_root)
    except ValueError:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PYTHON_OUTSIDE_WORKSPACE",
                                      message="benchmark python must be inside workspace/envs")
    if not launcher.exists():
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PYTHON_NOT_FOUND",
                                      message="benchmark python not found")
    if not _os.access(launcher, _os.X_OK):
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PYTHON_NOT_EXECUTABLE",
                                      message="benchmark python not executable")

    # Lockfile boundary
    config_root = (workspace_root.parent / "configs" / "benchmarks" / "environments").resolve(strict=True)
    lf = lockfile_path.resolve(strict=False)
    if not lf.is_file():
        raise BenchmarkPreflightError(check_name="environment", code="ENV_LOCKFILE_NOT_FOUND",
                                      message="lockfile not found")
    try:
        lf.resolve(strict=True).relative_to(config_root)
    except ValueError:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_LOCKFILE_OUTSIDE_CONFIG_ROOT",
                                      message="lockfile must be inside configs/benchmarks/environments")
    if lf.stat().st_size == 0:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_LOCKFILE_EMPTY",
                                      message="lockfile is empty")
    lock_sha = sha256_file(lf)

    # Run probe
    runner = probe_runner or _default_probe_runner
    try:
        result = runner(benchmark_python, _PROBE_SOURCE, timeout_seconds)
    except subprocess.TimeoutExpired:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PROBE_TIMEOUT",
                                      message="probe timed out")
    if result.returncode != 0:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PROBE_FAILED",
                                      message=f"probe exited {result.returncode}")

    try:
        data = _ProbeSchema.model_validate(json.loads(result.stdout))
    except Exception:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PROBE_INVALID_JSON",
                                      message="probe output does not match expected schema")

    # Package checks
    for pkg, code in [("torch", "ENV_TORCH_MISSING"), ("torchvision", "ENV_TORCHVISION_MISSING"),
                      ("faiss", "ENV_FAISS_MISSING"), ("timm", "ENV_TIMM_MISSING")]:
        if not getattr(data, f"{pkg}_present"):
            raise BenchmarkPreflightError(check_name="environment", code=code, message=f"{pkg} not available")

    # CUDA/GPU — read expected GPU from case
    expected_gpu = getattr(case, "fixed_parameters", {}).get("gpu", 0)
    if not data.cuda_available:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_CUDA_UNAVAILABLE",
                                      message="CUDA not available")
    if data.cuda_device_count < 1:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_CUDA_UNAVAILABLE",
                                      message="no CUDA devices")
    gpu_data = data.gpu or {}
    if gpu_data.get("index") != expected_gpu:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_GPU_INDEX_INVALID",
                                      message=f"expected GPU {expected_gpu}")
    if gpu_data.get("memory_mb", 0) <= 0:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_GPU_MEMORY_INVALID",
                                      message="GPU memory invalid")

    sha_data = {
        "python_version": data.python_version, "platform": data.platform,
        "torch": data.torch_version, "torchvision": data.torchvision_version,
        "faiss": data.faiss_version, "timm": data.timm_version,
        "cuda_runtime": data.cuda_runtime, "cuda_device_count": data.cuda_device_count,
        "gpu_index": gpu_data.get("index"), "gpu_name": gpu_data.get("name"),
        "gpu_memory": gpu_data.get("memory_mb"), "lockfile_sha256": lock_sha,
    }

    return BenchmarkEnvironmentSnapshot(
        schema_version=1, python_version=data.python_version,
        platform=data.platform, accelerator="cuda",
        torch_version=data.torch_version or "", torchvision_version=data.torchvision_version or "",
        cuda_available=True, cuda_device_count=data.cuda_device_count,
        gpu_index=expected_gpu, gpu_name=gpu_data.get("name"), gpu_memory_mb=gpu_data.get("memory_mb"),
        cuda_runtime=data.cuda_runtime, faiss_version=data.faiss_version,
        timm_version=data.timm_version, lockfile_sha256=lock_sha,
        environment_sha256=canonical_sha256(sha_data),
    )


def _default_probe_runner(python: Path, source: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(python), "-I", "-c", source], shell=False, check=False, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
