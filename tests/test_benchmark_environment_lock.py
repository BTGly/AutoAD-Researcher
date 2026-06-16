"""测试 environment lock contracts."""
from pathlib import Path

import pytest

from autoad_researcher.benchmarks.environment_lock import (
    BenchmarkEnvironmentSpec,
    compute_lockfile_sha256,
    validate_lockfile,
)


class TestSpec:
    def test_valid(self):
        s = BenchmarkEnvironmentSpec(
            schema_version=1, environment_id="env1", case_id="c1",
            platform="linux_x86_64", python_version="3.8.0",
            package_manager="uv", requirements_input_path="x.in",
            lockfile_path="x.txt", lockfile_sha256="a" * 64,
            required_imports=["torch"], accelerator="cuda", gpu_index=0,
            allow_network_during_build=True, allow_network_during_execution=False,
        )
        assert s.environment_id == "env1"

    def test_bad_env_id_rejected(self):
        with pytest.raises(Exception):
            BenchmarkEnvironmentSpec(
                schema_version=1, environment_id="Bad-ID", case_id="c1",
                platform="linux_x86_64", python_version="3.8",
                package_manager="uv", requirements_input_path="x",
                lockfile_path="x", lockfile_sha256="a" * 64,
                required_imports=["torch"], accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )


class TestValidateLockfile:
    def test_valid_lockfile(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==1.8.0+cu111\n")
        errors = validate_lockfile(lf)
        assert errors == []

    def test_loose_constraint_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch>=1.0\n")
        errors = validate_lockfile(lf)
        assert any("loose constraint" in e for e in errors)

    def test_git_dependency_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("git+https://github.com/x/y.git\n")
        errors = validate_lockfile(lf)
        assert any("git" in e for e in errors)

    def test_local_file_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("file:///usr/local/lib/x.whl\n")
        errors = validate_lockfile(lf)
        assert any("local file" in e for e in errors)

    def test_empty_lockfile_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("")
        with pytest.raises(ValueError, match="empty"):
            validate_lockfile(lf)

    def test_lockfile_sha_stable(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==1.8.0\n")
        assert compute_lockfile_sha256(lf) == compute_lockfile_sha256(lf)
