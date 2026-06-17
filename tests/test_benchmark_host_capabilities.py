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
        assert len(c.gpus) == 1
        assert c.gpus[0].index == 0

    def test_gpu_count_zero_with_empty_gpus_valid(self):
        """Schema allows gpu_count=0 (collector rejects it at runtime)."""
        c = BenchmarkHostCapabilities(
            schema_version=1, platform="linux_x86_64", machine="x86_64",
            uv_version="0.5", nvidia_driver_version="535",
            gpu_count=0, gpus=[],
            available_python_versions=["3.11.9"],
        )
        assert c.gpu_count == 0

    def test_negative_gpu_count_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkHostCapabilities(
                schema_version=1, platform="linux_x86_64", machine="x86_64",
                uv_version="0.5", nvidia_driver_version="535",
                gpu_count=-1,  # type: ignore[arg-type]
            )

    def test_empty_python_list_valid(self):
        """Schema allows empty Python list (collector detects at runtime)."""
        c = BenchmarkHostCapabilities(
            schema_version=1, platform="linux_x86_64", machine="x86_64",
            uv_version="0.5", nvidia_driver_version="535",
            gpu_count=1, gpus=[GpuCapability(index=0, name="A100", memory_mb=40960)],
            available_python_versions=[],
        )
        assert c.available_python_versions == []
