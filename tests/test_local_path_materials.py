import json
from pathlib import Path

from autoad_researcher.assistant.v2.job_service import (
    append_pipeline_job,
    fail_pipeline_job,
    retry_failed_source_jobs,
)
from autoad_researcher.assistant.v2.material_inspection import inspect_registered_material
from autoad_researcher.ui.sources import (
    inspect_local_path,
    load_source_registry,
    register_local_path_source,
)


def test_local_path_inspection_keeps_mixed_directory_as_multiple_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
    material = tmp_path / "research"
    material.mkdir()
    (material / "train.py").write_text("print('code')", encoding="utf-8")
    (material / "images.csv").write_text("name,label\na,0\n", encoding="utf-8")
    (material / "paper.pdf").write_bytes(b"%PDF")

    inspection = inspect_local_path(material)

    assert inspection["path_kind"] == "directory"
    assert inspection["detected_kind"] == "mixed"
    assert inspection["profiles"] == ["repository", "dataset", "document"]
    assert inspection["entry_count"] == 3
    assert inspection["confidence"] < 1


def test_local_path_registers_manifest_without_guessing_unknown_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source_path = tmp_path / "weights.bin"
    source_path.write_bytes(b"\x00\x01")

    source = register_local_path_source(run_dir, source_path)
    registry = load_source_registry(run_dir)

    assert source["kind"] == "local_path"
    assert source["inspection"]["detected_kind"] == "unknown"
    assert (run_dir / source["manifest_path"]).is_file()
    assert registry["sources"][0]["metadata"]["local_path_inspection"]["path_kind"] == "file"


def test_nested_image_directory_uses_content_evidence_for_dataset_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
    material = tmp_path / "images"
    (material / "class_a" / "train").mkdir(parents=True)
    for name in ("one.png", "two.png", "three.png"):
        (material / "class_a" / "train" / name).write_bytes(b"image")
    (material / "README.txt").write_text("image collection", encoding="utf-8")

    inspection = inspect_local_path(material)
    source = register_local_path_source(tmp_path / "run", material)

    assert inspection["detected_kind"] == "dataset"
    assert inspection["content_signals"]["image_files"] == 3
    assert source["kind"] == "dataset"
    assert source["inspection"]["profiles"] == ["dataset", "document"]


def test_material_inspection_executes_only_scoped_read_tools(tmp_path, monkeypatch):
    root = tmp_path / "material"
    root.mkdir()
    (root / "README.md").write_text("research notes", encoding="utf-8")
    calls = [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "filesystem_list", "arguments": '{"path":"."}'},
    }, {}]

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": "", "error": "", "tool_calls": [calls.pop(0)]} if calls[0] else {"reply": "done", "error": "", "tool_calls": []},
    )
    observations = inspect_registered_material(
        tmp_path / "run",
        source={
            "source_id": "src_material",
            "kind": "local_path",
            "original_reference": str(root),
            "inspection": {"detected_kind": "collection"},
        },
        user_input=f"检查 {root}",
        api_key="sk-test",
        provider_url="https://example.test",
        model="model",
    )

    assert observations[0]["tool"] == "filesystem_list"
    assert observations[0]["status"] == "success"
    permission_log = tmp_path / "run" / "assistant" / "permission_decisions.jsonl"
    assert permission_log.is_file()
    assert json.loads(permission_log.read_text(encoding="utf-8"))["tool_name"] == "filesystem_list"


def test_failed_source_retry_creates_new_generation_and_rewires_dependency(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    first = append_pipeline_job(run_dir, source_id="src_1", job_type="local_repo_acquire", evidence_role="repo_acquired")
    second = append_pipeline_job(
        run_dir,
        source_id="src_1",
        job_type="repo_summarize",
        evidence_role="repo_acquired",
        payload={"depends_on": first["job_id"]},
    )
    fail_pipeline_job(run_dir, first["job_id"], error="source disappeared")
    fail_pipeline_job(run_dir, second["job_id"], error="dependency failed")

    retry = retry_failed_source_jobs(run_dir, "src_1")

    assert [job["job_type"] for job in retry] == ["local_repo_acquire", "repo_summarize"]
    assert retry[0]["retry_of"] == first["job_id"]
    assert retry[1]["payload"]["depends_on"] == retry[0]["job_id"]
    assert retry[1]["retry_of"] == second["job_id"]
