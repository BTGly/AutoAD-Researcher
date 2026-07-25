from pathlib import Path

from autoad_researcher.ui.session_context import (
    attach_local_context,
    extract_local_path_candidates,
    load_session_context,
)


def test_attach_context_auto_allows_current_run_workspace_without_registry(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", raising=False)
    run_dir = tmp_path / "run"
    material = run_dir / "workspace" / "AnomalyCLIP"
    material.mkdir(parents=True)
    (material / "README.md").write_text("baseline", encoding="utf-8")

    receipt, context = attach_local_context(run_dir, material, user_label="baseline repo")

    assert receipt["status"] == "context_attached"
    assert receipt["access"] == "read_only"
    assert context is not None
    assert load_session_context(run_dir)[0]["path"] == str(material.resolve())
    assert not (run_dir / "sources" / "source_references.json").exists()


def test_attach_context_is_idempotent_and_accepts_explicit_user_path(tmp_path: Path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(allowed))
    run_dir = tmp_path / "run"
    material = allowed / "params.md"
    material.write_text("parameters", encoding="utf-8")

    first, _ = attach_local_context(run_dir, material)
    second, _ = attach_local_context(run_dir, material)
    rejected, context = attach_local_context(run_dir, outside / "missing.md")

    assert first["status"] == "context_attached"
    assert second["status"] == "context_already_attached"
    assert rejected["status"] == "failed"
    assert rejected["error"]["code"] == "LOCAL_PATH_NOT_FOUND"
    assert context is None
    assert len(load_session_context(run_dir)) == 1


def test_attach_context_allows_explicit_paths_without_special_runs_root_rules(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", raising=False)
    runs_root = tmp_path / "runs"
    monkeypatch.setenv("AUTOAD_RUNS_ROOT", str(runs_root))
    chat_run = runs_root / "chat_run"
    uat_material = runs_root / "uat_run" / "workspace" / "repos" / "AnomalyCLIP"
    uat_material.mkdir(parents=True)
    (uat_material / "README.md").write_text("baseline", encoding="utf-8")

    receipt, context = attach_local_context(chat_run, uat_material)

    assert receipt["status"] == "context_attached"
    assert context is not None
    outside = runs_root / "unmanaged.txt"
    outside.write_text("do not admit the whole runs root", encoding="utf-8")
    rejected, context = attach_local_context(chat_run, outside)
    assert rejected["status"] == "context_attached"
    assert context is not None


def test_extracts_multiple_absolute_paths_without_classifying_them(tmp_path: Path):
    repo = tmp_path / "repo"
    dataset = tmp_path / "dataset"
    paths = extract_local_path_candidates(f"仓库 {repo}，数据集 {dataset}，以及论文 /tmp/paper.pdf。")

    assert paths == [str(repo), str(dataset), "/tmp/paper.pdf"]


def test_extracts_explicit_relative_paths(tmp_path: Path):
    paths = extract_local_path_candidates("检查 ./repo、../dataset 和 workspace/config.yaml")

    assert paths == ["./repo", "../dataset", "workspace/config.yaml"]


def test_does_not_treat_slash_in_prose_as_root_path():
    assert extract_local_path_candidates("MVTec AD / bottle") == []
