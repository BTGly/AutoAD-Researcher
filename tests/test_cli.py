"""测试 AutoAD CLI。"""

import json

import pytest

from autoad_researcher.cli import main
from autoad_researcher.core import EventStore, PipelineController, PipelineResult


def test_smoke_success(tmp_path, capsys):
    exit_code = main(
        [
            "smoke",
            "--run-id",
            "run_demo",
            "--runs-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0

    output = capsys.readouterr().out
    assert "status: success" in output
    assert "experiment_planning" in output
    assert "patch_planning" in output

    run_dir = tmp_path / "run_demo"
    assert (run_dir / "experiment_plan.json").exists()
    assert (run_dir / "patch_plan.json").exists()
    assert (run_dir / "events.jsonl").exists()

    events = EventStore(runs_root=tmp_path).read_events("run_demo")
    assert [e.event_type for e in events] == [
        "run_created",
        "stage_started",
        "artifact_written",
        "stage_completed",
        "stage_started",
        "artifact_written",
        "stage_completed",
    ]


def test_smoke_json_output(tmp_path, capsys):
    exit_code = main(
        [
            "smoke",
            "--run-id",
            "run_json",
            "--runs-root",
            str(tmp_path),
            "--json",
        ]
    )

    assert exit_code == 0

    output = capsys.readouterr().out
    payload = json.loads(output)

    assert payload["run_id"] == "run_json"
    assert payload["status"] == "success"
    assert [s["stage"] for s in payload["stages"]] == [
        "experiment_planning",
        "patch_planning",
    ]
    assert payload["run_dir"] == str(tmp_path / "run_json")


def test_smoke_failed_pipeline_returns_one(tmp_path, monkeypatch, capsys):
    def fake_run(self, run_id):
        return PipelineResult(
            run_id=run_id,
            status="failed",
            stages=[],
            failed_stage="experiment_planning",
            error_type="RuntimeError",
            error_message="boom",
        )

    monkeypatch.setattr(
        PipelineController,
        "run_planning_pipeline",
        fake_run,
    )

    exit_code = main(
        [
            "smoke",
            "--run-id",
            "run_failed",
            "--runs-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1

    output = capsys.readouterr().out
    assert "status: failed" in output
    assert "failed_stage: experiment_planning" in output
    assert "error_message: boom" in output


def test_invalid_run_id_returns_two(tmp_path, capsys):
    exit_code = main(
        [
            "smoke",
            "--run-id",
            "../escape",
            "--runs-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "error:" in captured.err
    assert not (tmp_path.parent / "escape").exists()


def test_stage3_acceptance_help(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["stage3-acceptance", "--help"])

    assert excinfo.value.code == 0
    assert "stage3-acceptance" in capsys.readouterr().out


def test_stage3_acceptance_l1_l2_json_output(tmp_path, capsys):
    # Create input_task.yaml so intake passes; pipeline blocks at paper_intelligence (PDF outside tmp_path)
    run_dir = tmp_path / "run_310"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "input_task.yaml").write_text("run_id: run_310\nrequest: test\n", encoding="utf-8")

    exit_code = main(
        [
            "stage3-acceptance",
            "--run-id",
            "run_310",
            "--runs-root",
            str(tmp_path),
            "--mode",
            "l1-l2",
            "--json",
        ]
    )

    assert exit_code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run_310"
    assert payload["status"] == "blocked"
    assert payload["failed_stage"] == "paper_intelligence"
    assert "stage3_acceptance_manifest.json" in payload["artifacts"]


def test_stage3_acceptance_missing_artifact_returns_blocked(tmp_path, capsys):
    exit_code = main(
        [
            "stage3-acceptance",
            "--run-id",
            "run_missing",
            "--runs-root",
            str(tmp_path),
            "--mode",
            "l1-l2",
            "--require-artifact",
            "intake:input_task.yaml",
            "--json",
        ]
    )

    assert exit_code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["failure_reason"] == "blocked_missing_artifact:intake"
