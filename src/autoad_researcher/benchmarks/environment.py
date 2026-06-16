"""Benchmark environment preflight — probe isolated Python for deps, CUDA, GPU."""

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from autoad_researcher.benchmarks.errors import BenchmarkPreflightError
from autoad_researcher.benchmarks.evidence import BenchmarkEnvironmentSnapshot
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file

# Fixed probe source — NOT user-injectable
_PROBE_SOURCE = r"""
import json, sys, platform
result = {
    "python_version": platform.python_version(),
    "platform": f"{sys.platform}_{platform.machine()}",
}
# torch
try:
    import torch
    result["torch_version"] = torch.__version__
    result["torch_present"] = True
except ImportError:
    result["torch_version"] = None
    result["torch_present"] = False
# torchvision
try:
    import torchvision
    result["torchvision_version"] = torchvision.__version__
    result["torchvision_present"] = True
except ImportError:
    result["torchvision_version"] = None
    result["torchvision_present"] = False
# faiss
try:
    import faiss
    result["faiss_version"] = getattr(faiss, "__version__", "unknown")
    result["faiss_present"] = True
except ImportError:
    result["faiss_version"] = None
    result["faiss_present"] = False
# timm
try:
    import timm
    result["timm_version"] = timm.__version__
    result["timm_present"] = True
except ImportError:
    result["timm_version"] = None
    result["timm_present"] = False
# CUDA
try:
    import torch as _t
    result["cuda_available"] = _t.cuda.is_available()
    result["cuda_device_count"] = _t.cuda.device_count() if result["cuda_available"] else 0
    if result["cuda_available"] and result["cuda_device_count"] > 0:
        props = _t.cuda.get_device_properties(0)
        result["cuda_runtime"] = _t.version.cuda or str(_t._C._cuda_getDriverVersion())
        result["gpu"] = {"index": 0, "name": props.name, "memory_mb": props.total_memory // (1024*1024)}
    else:
        result["cuda_available"] = False
        result["cuda_device_count"] = 0
except Exception:
    result["cuda_available"] = False
    result["cuda_device_count"] = 0
json.dump(result, sys.stdout)
"""


def collect_environment_snapshot(
    *,
    case,
    benchmark_python: Path,
    lockfile_path: Path,
    workspace_root: Path,
    timeout_seconds: int = 30,
    probe_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> BenchmarkEnvironmentSnapshot:
    # Validate Python path boundary
    py = benchmark_python.resolve(strict=False)
    if not py.exists():
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PYTHON_NOT_FOUND",
                                      message="benchmark python not found")
    if not os.access(py, os.X_OK):
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PYTHON_NOT_EXECUTABLE",
                                      message="benchmark python not executable")
    try:
        py.relative_to(workspace_root / "envs")
    except ValueError:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PYTHON_OUTSIDE_WORKSPACE",
                                      message="benchmark python must be inside workspace/envs")

    # Validate lockfile
    lf = lockfile_path.resolve()
    if not lf.is_file():
        raise BenchmarkPreflightError(check_name="environment", code="ENV_LOCKFILE_NOT_FOUND",
                                      message="lockfile not found")
    try:
        lf.relative_to(workspace_root / ".." / "configs" / "benchmarks" / "environments")
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
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_PROBE_INVALID_JSON",
                                      message="probe output is not valid JSON")

    # Package checks
    for pkg, code in [("torch", "ENV_TORCH_MISSING"), ("torchvision", "ENV_TORCHVISION_MISSING"),
                      ("faiss", "ENV_FAISS_MISSING"), ("timm", "ENV_TIMM_MISSING")]:
        present = data.get(f"{pkg}_present")
        if not present:
            raise BenchmarkPreflightError(check_name="environment", code=code,
                                          message=f"{pkg} not available")

    # CUDA/GPU
    if not data.get("cuda_available"):
        raise BenchmarkPreflightError(check_name="environment", code="ENV_CUDA_UNAVAILABLE",
                                      message="CUDA not available")
    if data.get("cuda_device_count", 0) < 1:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_CUDA_UNAVAILABLE",
                                      message="no CUDA devices")
    gpu = data.get("gpu", {})
    if gpu.get("index") != 0:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_GPU_INDEX_INVALID",
                                      message="GPU index must be 0")
    if gpu.get("memory_mb", 0) <= 0:
        raise BenchmarkPreflightError(check_name="environment", code="ENV_GPU_MEMORY_INVALID",
                                      message="GPU memory invalid")

    sha_data = {
        "python_version": data["python_version"], "platform": data.get("platform", ""),
        "torch": data["torch_version"], "torchvision": data["torchvision_version"],
        "faiss": data["faiss_version"], "timm": data["timm_version"],
        "cuda_runtime": data.get("cuda_runtime", ""), "cuda_device_count": data["cuda_device_count"],
        "gpu_index": gpu.get("index"), "gpu_name": gpu.get("name"),
        "gpu_memory": gpu.get("memory_mb"), "lockfile_sha256": lock_sha,
    }

    return BenchmarkEnvironmentSnapshot(
        schema_version=1, python_version=data["python_version"],
        platform=data.get("platform", ""), accelerator="cuda",
        torch_version=data["torch_version"] or "", torchvision_version=data["torchvision_version"] or "",
        cuda_available=True, cuda_device_count=data["cuda_device_count"],
        gpu_index=0, gpu_name=gpu.get("name"), gpu_memory_mb=gpu.get("memory_mb"),
        cuda_runtime=data.get("cuda_runtime"), faiss_version=data.get("faiss_version"),
        timm_version=data.get("timm_version"), lockfile_sha256=lock_sha,
        environment_sha256=canonical_sha256(sha_data),
    )


def _default_probe_runner(python: Path, source: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(python), "-I", "-c", source],
        shell=False, check=False, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
