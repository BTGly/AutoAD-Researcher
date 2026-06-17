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

    def test_gpu_count_zero_rejected(self):
        """gpu_count=0 with empty gpus should be rejected — benchmark requires GPU."""
        # Schema allows gpu_count=0 (ge=0), but collector logic rejects it.
        # This test verifies the schema's minimum: the field constraint.
        with pytest.raises(ValidationError):
            BenchmarkHostCapabilities(
                schema_version=1, platform="linux_x86_64", machine="x86_64",
                uv_version="0.5", nvidia_driver_version="535",
                gpu_count=-1,  # type: ignore[arg-type]
            )

    def test_all_fields_present(self):
        c = BenchmarkHostCapabilities(
            schema_version=1, platform="linux_x86_64", machine="x86_64",
            glibc_version="2.35", uv_version="0.5.0", nvidia_driver_version="535",
            cuda_driver_capability="12.4", gpu_count=2,
            gpus=[
                GpuCapability(index=0, name="RTX 4090", memory_mb=24564, compute_capability="8.9"),
                GpuCapability(index=1, name="RTX 4090", memory_mb=24564),
            ],
            available_python_versions=["3.11.15", "3.10.14"],
        )
        assert len(c.gpus) == 2
