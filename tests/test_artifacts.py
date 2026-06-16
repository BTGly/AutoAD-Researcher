"""测试 ArtifactStore。"""

import pytest

from autoad_researcher.core import ArtifactStore, EventStore
from autoad_researcher.schemas import ExperimentPlan, PatchPlan


def make_experiment_plan() -> ExperimentPlan:
    return ExperimentPlan(
        experiment_goal="smoke",
        baseline="PatchCore",
        dataset="MVTec AD",
        categories=["bottle"],
        metrics=["image-level AUROC"],
        control_group="baseline",
        experiment_group="experiment",
        resource_budget="single GPU",
        risks=["implementation risk"],
    )


def make_patch_plan() -> PatchPlan:
    return PatchPlan(
        target_repo="example",
        files_to_inspect=["README.md"],
        files_to_modify=["README.md"],
        planned_changes=["add note"],
        expected_risks=["none"],
        requires_approval=True,
    )


class TestArtifactStore:
    def test_write_and_read_experiment_plan(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)
        plan = make_experiment_plan()

        path = store.write_json("run_demo", "experiment_plan.json", plan)

        assert path == tmp_path / "run_demo" / "experiment_plan.json"
        assert path.exists()

        loaded = store.read_model("run_demo", "experiment_plan.json", ExperimentPlan)
        assert loaded.experiment_goal == "smoke"
        assert loaded.baseline == "PatchCore"

    def test_write_and_read_patch_plan(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)
        patch = make_patch_plan()

        path = store.write_json("run_demo", "patch_plan.json", patch)

        assert path == tmp_path / "run_demo" / "patch_plan.json"
        assert path.exists()

        loaded = store.read_model("run_demo", "patch_plan.json", PatchPlan)
        assert loaded.requires_approval is True

    def test_write_dict_json(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)

        store.write_json(
            "run_demo",
            "paper_summary.json",
            {
                "title": "example",
                "core_idea": "test",
            },
        )

        data = store.read_json("run_demo", "paper_summary.json")
        assert data["title"] == "example"

    def test_overwrite_false_rejects_existing_file(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)
        plan = make_experiment_plan()

        store.write_json("run_demo", "experiment_plan.json", plan)

        with pytest.raises(FileExistsError):
            store.write_json(
                "run_demo",
                "experiment_plan.json",
                plan,
                overwrite=False,
            )

    @pytest.mark.parametrize(
        "bad_run_id",
        [
            "",
            ".",
            "..",
            "...",
            "../escape",
            "foo/bar",
            "foo\\bar",
        ],
    )
    def test_invalid_run_id_rejected(self, tmp_path, bad_run_id):
        store = ArtifactStore(runs_root=tmp_path)

        with pytest.raises(ValueError):
            store.write_json(
                bad_run_id,
                "experiment_plan.json",
                make_experiment_plan(),
            )

    @pytest.mark.parametrize(
        "bad_filename",
        [
            "../escape.json",
            "/tmp/escape.json",
            "nested/file.json",
            "unknown.json",
            "",
        ],
    )
    def test_invalid_artifact_filename_rejected(self, tmp_path, bad_filename):
        store = ArtifactStore(runs_root=tmp_path)

        with pytest.raises(ValueError):
            store.write_json(
                "run_demo",
                bad_filename,
                make_experiment_plan(),
            )

    def test_read_missing_artifact_raises(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)

        with pytest.raises(FileNotFoundError):
            store.read_json("run_demo", "experiment_plan.json")

    def test_write_json_records_artifact_written_event(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)
        plan = make_experiment_plan()

        store.write_json("run_demo", "experiment_plan.json", plan)

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert len(events) == 1
        assert events[0].event_type == "artifact_written"
        assert events[0].payload["artifact"] == "experiment_plan.json"
        assert events[0].payload["overwrite"] is True

    def test_read_json_records_artifact_read_event(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path)
        plan = make_experiment_plan()

        store.write_json("run_demo", "experiment_plan.json", plan)
        store.read_json("run_demo", "experiment_plan.json")

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert [e.event_type for e in events] == [
            "artifact_written",
            "artifact_read",
        ]
        assert events[1].payload["artifact"] == "experiment_plan.json"

    def test_events_can_be_disabled(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        plan = make_experiment_plan()

        store.write_json("run_demo", "experiment_plan.json", plan)
        store.read_json("run_demo", "experiment_plan.json")

        event_path = tmp_path / "run_demo" / "events.jsonl"
        assert not event_path.exists()
