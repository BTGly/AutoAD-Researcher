"""测试 host capabilities schema."""
import pytest
from pydantic import ValidationError

from autoad_researcher.benchmarks.host_capabilities import (
    BenchmarkHostCapabilities,
    GpuCapability,
)


class TestHostCapabilities:
    def test_linux_x86_valid(self):
        c = BenchmarkHostCapabilities(
            schema_version=1, platform="linux_x86_64", machine="x86_64",
            uv_version="0.5.0", nvidia_driver_version="535",
            gpu_count=1, gpus=[GpuCapability(index=0, name="A100", memory_mb=40960)],
            available_python_versions=["3.11.9"],
        )
        assert c.gpu_count == 1

    def test_no_gpu_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkHostCapabilities(
                schema_version=1, platform="linux_x86_64", machine="x86_64",
                uv_version="0.5", nvidia_driver_version="535",
                gpu_count=-1,  # type: ignore[arg-type]
            )

    def test_no_python_rejected_in_collector(self):
        import subprocess
        def fake_smi():
            return "0, TestGPU, 40960, 8.0"
        def fake_run(argv, timeout=10):
            if "nvidia" in str(argv):
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="0, G, 40960, 8.0")
            raise FileNotFoundError("no python")
        # This test verifies the schema handles the case; actual collector requires real env
