"""Host capabilities inspection — collect platform, GPU, Python version info."""
import json
import platform
import struct
import subprocess
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field


class GpuCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    index: int = Field(ge=0)
    name: str = Field(min_length=1)
    memory_mb: int = Field(ge=0)
    compute_capability: str | None = None


class BenchmarkHostCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    schema_version: Literal[1]
    platform: Literal["linux_x86_64"]
    machine: Literal["x86_64"]
    glibc_version: str | None = None
    available_python_versions: list[str] = Field(default_factory=list)
    uv_version: str = Field(min_length=1)
    nvidia_driver_version: str = Field(min_length=1)
    cuda_driver_capability: str | None = None
    gpu_count: int = Field(ge=0)
    gpus: list[GpuCapability] = Field(default_factory=list)


def _run(argv: list[str], timeout: int = 10) -> str:
    return subprocess.run(argv, shell=False, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def collect_host_capabilities(
    nvidia_smi_runner: Callable[[], str] | None = None,
) -> BenchmarkHostCapabilities:
    if platform.system().lower() != "linux" or platform.machine() != "x86_64":
        raise ValueError("host must be linux_x86_64")

    # uv version
    uv_ver = _run(["uv", "--version"]).split(" ")[1]

    # Python versions
    py_versions = []
    for cmd in ["python3.11", "python3.10", "python3.9", "python3.8", "python3.12"]:
        try:
            v = _run([cmd, "-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"])
            py_versions.append(v)
        except Exception:
            pass
    if not py_versions:
        try:
            v = _run(["python3", "-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"])
            py_versions.append(v)
        except Exception:
            raise ValueError("no usable Python found")

    # GPU
    try:
        nvidia_smi = nvidia_smi_runner() if nvidia_smi_runner else _run(["nvidia-smi", "--query-gpu=index,name,memory.total,compute_cap", "--format=csv,noheader,nounits"])
    except Exception:
        raise ValueError("nvidia-smi failed; GPU required for this benchmark case")

    gpus = []
    driver_ver = "unknown"
    for line in nvidia_smi.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append(GpuCapability(
                index=int(parts[0]), name=parts[1], memory_mb=int(parts[2]),
                compute_capability=parts[3] if parts[3] else None,
            ))
        if "Driver Version" in line:
            driver_ver = line.split(":")[-1].strip()

    # Try to get driver version from nvidia-smi directly
    try:
        driver_out = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
        driver_ver = driver_out.splitlines()[0].strip()
    except Exception:
        pass

    if not gpus:
        raise ValueError("GPU 0 required for this benchmark case")

    glibc = None
    try:
        glibc = _run(["/lib/x86_64-linux-gnu/libc.so.6"]) if Path("/lib/x86_64-linux-gnu/libc.so.6").exists() else None
    except Exception:
        pass

    return BenchmarkHostCapabilities(
        schema_version=1, platform="linux_x86_64", machine="x86_64",
        glibc_version=glibc, available_python_versions=py_versions, uv_version=uv_ver,
        nvidia_driver_version=driver_ver, cuda_driver_capability=None,
        gpu_count=len(gpus), gpus=gpus,
    )
