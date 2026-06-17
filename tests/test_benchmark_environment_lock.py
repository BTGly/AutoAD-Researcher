"""测试 environment lock contracts."""
from pathlib import Path

import pytest

from autoad_researcher.benchmarks.environment_lock import (
    BenchmarkEnvironmentSpec,
    PackageIndexSpec,
    compute_lockfile_sha256,
    parse_lockfile_pins,
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
            package_manager_version="0.5.0",
            package_indexes=[PackageIndexSpec(name="pypi", url="https://pypi.org/simple", default=True)],
            requirements_input_path="x.in", lockfile_path="x.txt",
            lockfile_sha256="a" * 64, required_imports=["torch"],
            accelerator="cuda", gpu_index=0,
            allow_network_during_build=True, allow_network_during_execution=False,
        )
        assert s.lockfile_sha256 == "a" * 64

    def test_draft_must_not_have_locked_fields(self):
        with pytest.raises(ValueError, match="draft spec"):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="draft", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
                requirements_input_path="x.in", lockfile_path="x.txt",
                lockfile_sha256="a" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_locked_empty_python_version_rejected(self):
        with pytest.raises(Exception):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="", package_manager="uv",
                requirements_input_path="x", lockfile_path="x",
                lockfile_sha256="a" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_sha_must_be_hex(self):
        with pytest.raises(Exception):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
                requirements_input_path="x", lockfile_path="x",
                lockfile_sha256="z" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_nested_parent_traversal_rejected(self):
        with pytest.raises(Exception):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="draft", environment_id="env1", case_id="c1",
                platform="linux_x86_64", package_manager="uv",
                requirements_input_path="foo/../outside", lockfile_path="x.txt",
                required_imports=["torch"], accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )


class TestValidateLockfile:
    def test_exact_pin_valid(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==1.8.0\n")
        assert validate_lockfile(lf) == []

    def test_exact_pin_with_marker_valid(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text('importlib-metadata==8.7.0; python_version < "3.10"\n')
        assert validate_lockfile(lf) == []

    def test_loose_marker_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text('importlib-metadata>=8.0; python_version < "3.10"\n')
        errors = validate_lockfile(lf)
        assert any("exact ==" in e for e in errors)

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

    def test_editable_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("--editable ../local\n")
        errors = validate_lockfile(lf)
        assert any("prohibited" in e for e in errors)

    def test_include_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("--requirement other.txt\n")
        errors = validate_lockfile(lf)
        assert any("prohibited" in e for e in errors)

    def test_index_url_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("--index-url https://example.com/simple\n")
        errors = validate_lockfile(lf)
        assert any("prohibited" in e for e in errors)

    def test_no_pinned_deps_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("# no deps\n--index-url https://x.com\n")
        errors = validate_lockfile(lf)
        assert any("no pinned" in e for e in errors)

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

    def test_locked_requires_indexes(self):
        with pytest.raises(ValueError, match="package_indexes"):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
                package_manager_version="0.5",
                requirements_input_path="x.in", lockfile_path="x.txt",
                lockfile_sha256="a" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_locked_requires_exactly_one_default_index(self):
        with pytest.raises(ValueError, match="exactly one default"):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
                package_manager_version="0.5",
                package_indexes=[
                    PackageIndexSpec(name="a", url="https://a.com", default=True),
                    PackageIndexSpec(name="b", url="https://b.com", default=True),
                ],
                requirements_input_path="x.in", lockfile_path="x.txt",
                lockfile_sha256="a" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_index_url_credentials_rejected(self):
        with pytest.raises(Exception):
            PackageIndexSpec(name="x", url="https://user:pass@example.com/simple")

    def test_index_url_query_rejected(self):
        with pytest.raises(Exception):
            PackageIndexSpec(name="x", url="https://example.com/simple?token=secret")

    def test_duplicate_index_name_rejected(self):
        with pytest.raises(ValueError, match="duplicate package index name"):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
                package_manager_version="0.5",
                package_indexes=[
                    PackageIndexSpec(name="pypi", url="https://pypi.org/simple", default=True),
                    PackageIndexSpec(name="pypi", url="https://download.pytorch.org/whl/cu124"),
                ],
                requirements_input_path="x.in", lockfile_path="x.txt",
                lockfile_sha256="a" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_duplicate_index_url_rejected(self):
        with pytest.raises(ValueError, match="duplicate package index URL"):
            BenchmarkEnvironmentSpec(
                schema_version=1, status="locked", environment_id="env1", case_id="c1",
                platform="linux_x86_64", python_version="3.8.0", package_manager="uv",
                package_manager_version="0.5",
                package_indexes=[
                    PackageIndexSpec(name="primary", url="https://pypi.org/simple", default=True),
                    PackageIndexSpec(name="mirror", url="https://pypi.org/simple"),
                ],
                requirements_input_path="x.in", lockfile_path="x.txt",
                lockfile_sha256="a" * 64, required_imports=["torch"],
                accelerator="cuda", gpu_index=0,
                allow_network_during_build=True, allow_network_during_execution=False,
            )

    def test_index_url_fragment_rejected(self):
        with pytest.raises(Exception):
            PackageIndexSpec(name="x", url="https://example.com/simple#token")

    def test_parse_lockfile_pins(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("Torch==2.5.1+cu124\nfaiss-cpu==1.14.3\n", encoding="utf-8")

        assert parse_lockfile_pins(lf) == {
            "torch": "==2.5.1+cu124",
            "faiss-cpu": "==1.14.3",
        }

    def test_comment_cannot_spoof_required_version(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text(
            "# expected torch==2.5.1+cu124\n"
            "torch==2.5.0+cu124\n",
            encoding="utf-8",
        )

        pins = parse_lockfile_pins(lf)

        assert pins["torch"] == "==2.5.0+cu124"
        assert pins["torch"] != "==2.5.1+cu124"

    def test_duplicate_package_pin_rejected(self, tmp_path):
        lf = tmp_path / "lock.txt"
        lf.write_text("torch==2.5.1+cu124\nTorch==2.5.0+cu124\n", encoding="utf-8")

        with pytest.raises(ValueError, match="duplicate package pin: torch"):
            parse_lockfile_pins(lf)
