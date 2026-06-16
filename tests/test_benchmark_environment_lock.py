"""测试 environment lock contracts."""
from pathlib import Path

import pytest

from autoad_researcher.benchmarks.environment_lock import (
    BenchmarkEnvironmentSpec,
    compute_lockfile_sha256,
    validate_lockfile,
)


class TestSpec:
    def test_draft_valid(self):
        s = BenchmarkEnvironmentSpec(
            schema_version=1, status="draft", environment_id="env1", case_id="c1",
            platform="linux_x86_64", package_manager="uv",
            requirements_input_path="x.in", lockfile_path="x.txt",
            required_imports=["torch"], accelerator="cuda", gpu_index=0,
            allow_network_during_build=True, allow_network_during_execution=False,
        )
        assert s.python_version is None

    def test_locked_requires_fields(self):
        with pytest.raises(ValueError, match="python_version"):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", package_manager="uv",
                requirements_input_path="x.in", lockfile_path="x.txt",
                required_imports=["torch"], accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_locked_valid(self):
        s = BenchmarkEnvironmentSpec(
            schema_version=1, status="locked", environment_id="env1", case_id="c1",
            platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
            requirements_input_path="x.in", lockfile_path="x.txt",
            lockfile_sha256="a" * 64, required_imports=["torch"],
            accelerator="cuda", gpu_index=0,
            allow_network_during_build=True, allow_network_during_execution=False,
        )
        assert s.lockfile_sha256 == "a" * 64

    def test_sha_must_be_hex(self):
        with pytest.raises(Exception):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8", package_manager="uv",
                requirements_input_path="x", lockfile_path="x",
                lockfile_sha256="z" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_absolute_path_rejected(self):
        with pytest.raises(Exception):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="draft", environment_id="env1", case_id="c1",
                platform="linux_x86_64", package_manager="uv",
                requirements_input_path="/etc/x", lockfile_path="/x",
                required_imports=["torch"], accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )


class TestValidateLockfile:
    def test_exact_pin_valid(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==1.8.0\n")
        assert validate_lockfile(lf) == []

    def test_loose_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch>=1.0\n")
        errors = validate_lockfile(lf)
        assert any("exact ==" in e for e in errors)

    def test_git_url_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch @ git+https://github.com/x.git\n")
        errors = validate_lockfile(lf)
        assert any("URL" in e for e in errors)

    def test_file_url_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch @ file:///tmp/x.whl\n")
        errors = validate_lockfile(lf)
        assert any("URL" in e for e in errors)

    def test_bare_dependency_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch\n")
        errors = validate_lockfile(lf)
        assert any("exactly one" in e for e in errors)

    def test_wildcard_version_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==2.*\n")
        errors = validate_lockfile(lf)
        assert any("exact ==" in e for e in errors)

    def test_empty_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("")
        with pytest.raises(ValueError, match="empty"):
            validate_lockfile(lf)

    def test_sha_stable(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==1.8.0\n")
        assert compute_lockfile_sha256(lf) == compute_lockfile_sha256(lf)
