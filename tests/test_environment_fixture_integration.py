"""Offline integration tests for environment plan fixtures."""

from pathlib import Path

from autoad_researcher.environments import (
    CommandExecutionOutput,
    ResolvedCommand,
    ValidationContext,
    load_environment_plan,
    run_environment_build_steps,
    validate_environment,
)

FIXTURE_DIR = Path("fixtures/environment_plans")


def success_runner(command: ResolvedCommand) -> CommandExecutionOutput:
    return CommandExecutionOutput(
        exit_code=0,
        stdout=f"{command.step_id} ok\n",
        stderr="",
    )


def test_cpu_uv_fixture_builds_and_validates_offline(tmp_path: Path):
    plan = load_environment_plan(FIXTURE_DIR / "python_cpu_uv.yaml")

    build = run_environment_build_steps(plan, tmp_path / "build", runner=success_runner)
    validation = validate_environment(
        plan,
        ValidationContext(
            runtime_versions={"python": "3.11.9"},
            importable_modules=["cpu_fixture_project"],
        ),
        tmp_path / "validation",
    )

    assert build.status == "success"
    assert build.snapshot_path is None
    assert validation.status == "passed"


def test_cuda_fixture_builds_and_validates_with_fake_gpu_context(tmp_path: Path):
    plan = load_environment_plan(FIXTURE_DIR / "python_cuda_uv.yaml")

    build = run_environment_build_steps(plan, tmp_path / "build", runner=success_runner)
    validation = validate_environment(
        plan,
        ValidationContext(
            importable_modules=["torch", "torchvision", "timm", "faiss"],
            gpu_compute_ok=True,
        ),
        tmp_path / "validation",
    )

    assert build.status == "success"
    assert validation.status == "passed"
    assert validation.results[-1].kind == "gpu_compute"


def test_existing_python_fixture_builds_and_validates_offline(tmp_path: Path):
    plan = load_environment_plan(FIXTURE_DIR / "existing_python.yaml")

    build = run_environment_build_steps(plan, tmp_path / "build", runner=success_runner)
    validation = validate_environment(
        plan,
        ValidationContext(
            runtime_versions={"python": "3.11.0"},
            importable_modules=["pydantic"],
        ),
        tmp_path / "validation",
    )

    assert build.status == "success"
    assert validation.status == "passed"
    assert (tmp_path / "validation" / "validation_report.json").is_file()


def test_fixture_build_failure_preserves_failed_evidence(tmp_path: Path):
    plan = load_environment_plan(FIXTURE_DIR / "python_cpu_uv.yaml")

    def failed_runner(command: ResolvedCommand) -> CommandExecutionOutput:
        return CommandExecutionOutput(exit_code=1, stdout="", stderr="install failed")

    build = run_environment_build_steps(plan, tmp_path / "build", runner=failed_runner)

    assert build.status == "failed"
    assert build.snapshot_path is None
    assert (tmp_path / "build" / "step_results.json").is_file()
    assert (tmp_path / "build" / "logs" / "create_venv.stderr.log").read_text(
        encoding="utf-8"
    ) == "install failed"
