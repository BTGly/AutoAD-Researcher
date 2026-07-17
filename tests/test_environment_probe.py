"""Evidence and observed-value tests for the environment probe layer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from autoad_researcher.environments.context_collector import collect_validation_context
from autoad_researcher.environments.models import EnvironmentPlan
from autoad_researcher.environments.probe import probe_host, probe_repository
from tests.test_environment_plan_models import valid_plan


def _host_runner(argv: list[str], cwd: Path | None, timeout: int):
    del cwd, timeout
    if argv[0] == "uv":
        return subprocess.CompletedProcess(argv, 0, "uv 0.9.0 API_KEY=sk-test-secret\n", "")
    if argv[0] == "conda":
        raise FileNotFoundError("conda")
    if argv[0] == "nvidia-smi":
        return subprocess.CompletedProcess(argv, 0, "NVIDIA A100, 550.54, 8.0, 40960\n", "")
    if "torch_present" in argv[-1]:
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({"torch_present": True, "cuda_available": True, "gpu_compute_ok": False}),
            "",
        )
    if "platform.system" in argv[-1]:
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({"system": "Linux", "release": "x", "machine": "x86_64", "python": "3.12", "executable": "/python"}),
            "",
        )
    return subprocess.CompletedProcess(argv, 0, "pip 24\n", "")


def test_host_probe_separates_detected_gpu_from_torch_compute(tmp_path: Path):
    probe = probe_host(tmp_path, python_executable="python", runner=_host_runner)

    assert probe.gpu_available is True
    assert probe.torch["gpu_compute_ok"] is False
    assert probe.gpu_capability[0]["compute_capability"] == "8.0"
    assert "sk-test-secret" not in (tmp_path / "logs" / "uv.stdout.log").read_text(encoding="utf-8")
    assert "[REDACTED]" in (tmp_path / "logs" / "uv.stdout.log").read_text(encoding="utf-8")


def test_repository_probe_reuses_repository_structure_profile(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repository / "README.md").write_text("demo\n", encoding="utf-8")
    (repository / "train.py").write_text("if __name__ == '__main__': pass\n", encoding="utf-8")

    def runner(argv: list[str], cwd: Path | None, timeout: int):
        del cwd, timeout
        output = "deadbeef\n" if argv[1:3] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    probe = probe_repository(repository, tmp_path / "probe", source_id="source_demo", runner=runner)

    assert probe.repository_commit == "deadbeef"
    assert probe.dependency_files == ["pyproject.toml"]
    assert probe.readme_files == ["README.md"]
    assert probe.entrypoint_candidates == ["train.py"]
    assert probe.project_smoke_candidates == ["train.py"]


def test_context_collector_uses_observed_torch_compute_result(tmp_path: Path):
    plan_data = valid_plan()
    plan_data["validation_steps"].append({
        "validation_id": "import_json",
        "kind": "python_import",
        "parameters": {"modules": ["json"]},
        "required": True,
        "timeout_seconds": 30,
        "network": False,
    })
    plan = EnvironmentPlan.model_validate(plan_data)

    def runner(argv: list[str], cwd: Path | None, timeout: int):
        del cwd, timeout
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({
                "runtime_versions": {"python": "3.11.9", "platform": "linux_x86_64"},
                "packages": {"pydantic": "2.11"},
                "importable_modules": ["json"],
                "torch": {"cuda_available": True, "gpu_compute_ok": False},
            }),
            "",
        )

    collected = collect_validation_context(
        plan,
        python_executable="python",
        repository_probe=None,
        output_dir=tmp_path / "context",
        runner=runner,
    )

    assert collected.context.gpu_available is True
    assert collected.context.gpu_compute_ok is False
    assert collected.context.importable_modules == ["json"]
    assert (tmp_path / "context" / "validation_context.json").is_file()
