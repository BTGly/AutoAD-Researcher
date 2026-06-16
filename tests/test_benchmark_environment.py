"""测试 environment preflight."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoad_researcher.benchmarks.environment import collect_environment_snapshot
from autoad_researcher.benchmarks.errors import BenchmarkPreflightError


def _cuda_probe():
    return json.dumps({
        "python_version": "3.8.0", "platform": "linux_x86_64",
        "torch_version": "1.8.0", "torch_present": True,
        "torchvision_version": "0.9.0", "torchvision_present": True,
        "faiss_version": "1.7.0", "faiss_present": True,
        "timm_version": "0.4.0", "timm_present": True,
        "cuda_available": True, "cuda_device_count": 1,
        "cuda_runtime": "11.0",
        "gpu": {"index": 0, "name": "TestGPU", "memory_mb": 8192},
    })


def _case(gpu_index=0):
    return SimpleNamespace(fixed_parameters={"gpu": gpu_index})


class TestEnvironmentPreflight:
    def test_cuda_probe_passes(self, tmp_path):
        (tmp_path / "workspace" / "envs" / "benchmark" / "bin").mkdir(parents=True)
        py = tmp_path / "workspace" / "envs" / "benchmark" / "bin" / "python"
        py.write_text("fake"); py.chmod(0o755)
        (tmp_path / "configs" / "benchmarks" / "environments" / "e").mkdir(parents=True)
        env_root = tmp_path / "configs" / "benchmarks" / "environments" / "env1"
        env_root.mkdir(parents=True)
        lf = env_root / "lock.txt"; lf.write_text("torch==1.8.0")
        def runner(python, source, timeout):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=_cuda_probe(), stderr="")
        result = collect_environment_snapshot(
            case=_case(), benchmark_python=py, lockfile_path=lf,
            workspace_root=tmp_path / "workspace", probe_runner=runner,
        )
        assert result is not None

    def test_python_outside_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "envs").mkdir(parents=True)
        py = tmp_path / "outside_python"
        py.write_text("fake")
        py.chmod(0o755)

        env_root = tmp_path / "configs" / "benchmarks" / "environments" / "e"
        env_root.mkdir(parents=True)
        lf = env_root / "lock.txt"
        lf.write_text("x")

        with pytest.raises(BenchmarkPreflightError, match="inside workspace/envs"):
            collect_environment_snapshot(
                case=_case(), benchmark_python=py, lockfile_path=lf,
                workspace_root=tmp_path / "workspace",
            )

    def test_lockfile_not_found(self, tmp_path):
        (tmp_path / "workspace" / "envs" / "benchmark" / "bin").mkdir(parents=True)
        py = tmp_path / "workspace" / "envs" / "benchmark" / "bin" / "python"
        py.write_text("fake"); py.chmod(0o755); (tmp_path / "configs" / "benchmarks" / "environments" / "e").mkdir(parents=True)
        with pytest.raises(BenchmarkPreflightError, match="not found"):
            collect_environment_snapshot(
                case=_case(), benchmark_python=py,
                lockfile_path=tmp_path / "nope.txt",
                workspace_root=tmp_path / "workspace",
            )

    def test_probe_torch_missing(self, tmp_path):
        ws = tmp_path / "workspace"
        (tmp_path / "workspace" / "envs" / "benchmark" / "bin").mkdir(parents=True)
        py = tmp_path / "workspace" / "envs" / "benchmark" / "bin" / "python"
        py.write_text("fake")
        py.chmod(0o755)

        env_root = tmp_path / "configs" / "benchmarks" / "environments" / "e"
        env_root.mkdir(parents=True)
        lf = env_root / "lock.txt"
        lf.write_text("x")

        no_torch = json.dumps({
            "python_version": "3.8", "platform": "linux",
            "torch_version": None, "torch_present": False,
            "torchvision_version": None, "torchvision_present": False,
            "faiss_version": None, "faiss_present": False,
            "timm_version": None, "timm_present": False,
            "cuda_available": False, "cuda_device_count": 0, "cuda_runtime": None, "gpu": None,
        })
        def runner(python, source, timeout):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=no_torch, stderr="")

        with pytest.raises(BenchmarkPreflightError, match="torch not available"):
            collect_environment_snapshot(
                case=_case(), benchmark_python=py, lockfile_path=lf,
                workspace_root=tmp_path / "workspace", probe_runner=runner,
            )

    def test_probe_timeout(self, tmp_path):
        ws = tmp_path / "workspace"
        (tmp_path / "workspace" / "envs" / "benchmark" / "bin").mkdir(parents=True)
        py = tmp_path / "workspace" / "envs" / "benchmark" / "bin" / "python"
        py.write_text("fake")
        py.chmod(0o755)

        env_root = tmp_path / "configs" / "benchmarks" / "environments" / "e"
        env_root.mkdir(parents=True)
        lf = env_root / "lock.txt"
        lf.write_text("x")

        def runner(python, source, timeout):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        with pytest.raises(BenchmarkPreflightError, match="timed out"):
            collect_environment_snapshot(
                case=_case(), benchmark_python=py, lockfile_path=lf,
                workspace_root=tmp_path / "workspace", probe_runner=runner, timeout_seconds=1,
            )

    def test_sha_stable(self, tmp_path):
        ws = tmp_path / "workspace"
        (tmp_path / "workspace" / "envs" / "benchmark" / "bin").mkdir(parents=True)
        py = tmp_path / "workspace" / "envs" / "benchmark" / "bin" / "python"
        py.write_text("fake")
        py.chmod(0o755)

        env_root = tmp_path / "configs" / "benchmarks" / "environments" / "e"
        env_root.mkdir(parents=True)
        lf = env_root / "lock.txt"
        lf.write_text("torch==1.8.0")

        def runner(python, source, timeout):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=_cuda_probe(), stderr="")

        s1 = collect_environment_snapshot(case=_case(), benchmark_python=py, lockfile_path=lf,
                                          workspace_root=tmp_path / "workspace", probe_runner=runner)
        s2 = collect_environment_snapshot(case=_case(), benchmark_python=py, lockfile_path=lf,
                                          workspace_root=tmp_path / "workspace", probe_runner=runner)
        assert s1.environment_sha256 == s2.environment_sha256

    def test_symlink_launcher_allowed(self, tmp_path):
        (tmp_path / "workspace" / "envs" / "benchmark" / "bin").mkdir(parents=True)
        real_py = tmp_path / "real_python"
        real_py.write_text("fake"); real_py.chmod(0o755)
        py = tmp_path / "workspace" / "envs" / "benchmark" / "bin" / "python"
        py.symlink_to(real_py)
        (tmp_path / "configs" / "benchmarks" / "environments" / "e").mkdir(parents=True)
        lf = tmp_path / "configs" / "benchmarks" / "environments" / "e" / "lock.txt"
        lf.write_text("x")

        def runner(python, source, timeout):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=_cuda_probe(), stderr="")

        result = collect_environment_snapshot(
            case=_case(), benchmark_python=py, lockfile_path=lf,
            workspace_root=tmp_path / "workspace", probe_runner=runner,
        )
        assert result is not None

    def test_intermediate_dir_symlink_rejected(self, tmp_path):
        (tmp_path / "outside" / "benchmark" / "bin").mkdir(parents=True)
        py = tmp_path / "outside" / "benchmark" / "bin" / "python"
        py.write_text("fake"); py.chmod(0o755)
        (tmp_path / "workspace" / "envs").mkdir(parents=True)
        link = tmp_path / "workspace" / "envs" / "benchmark"
        link.symlink_to(tmp_path / "outside" / "benchmark")
        (tmp_path / "configs" / "benchmarks" / "environments" / "e").mkdir(parents=True)
        lf = tmp_path / "configs" / "benchmarks" / "environments" / "e" / "lock.txt"
        lf.write_text("x")

        with pytest.raises(BenchmarkPreflightError, match="inside workspace/envs"):
            collect_environment_snapshot(
                case=_case(), benchmark_python=link / "bin" / "python", lockfile_path=lf,
                workspace_root=tmp_path / "workspace",
            )

    def test_missing_launcher_preserves_not_found(self, tmp_path):
        (tmp_path / "workspace" / "envs" / "b" / "bin").mkdir(parents=True)
        py = tmp_path / "workspace" / "envs" / "b" / "bin" / "python"
        # File does NOT exist
        (tmp_path / "configs" / "benchmarks" / "environments" / "e").mkdir(parents=True)
        lf = tmp_path / "configs" / "benchmarks" / "environments" / "e" / "lock.txt"
        lf.write_text("x")

        with pytest.raises(BenchmarkPreflightError, match="not found"):
            collect_environment_snapshot(
                case=_case(), benchmark_python=py, lockfile_path=lf,
                workspace_root=tmp_path / "workspace",
            )
