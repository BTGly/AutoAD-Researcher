from pathlib import Path

from autoad_researcher.repository_intelligence.structure_profile import (
    build_repository_structure_profile,
)


def test_structure_profile_reports_candidates_without_selecting_a_primary(tmp_path: Path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    (tmp_path / "train.py").write_text("def main(): pass\n", encoding="utf-8")
    (tmp_path / "src" / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    (tmp_path / "bin" / "run_demo.py").write_text(
        "def cli(): pass\n\nif __name__ == '__main__':\n    cli()\n",
        encoding="utf-8",
    )
    (tmp_path / "launch_experiment.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "configs" / "baseline.yaml").write_text("seed: 1\n", encoding="utf-8")
    (tmp_path / "requirements_dev.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[project.scripts]\ndemo-train='src.main:main'\n",
        encoding="utf-8",
    )

    profile = build_repository_structure_profile(
        repository_root=tmp_path,
        source_id="src_repo",
        source_fingerprint="a" * 64,
    )

    assert profile.entrypoint_candidates == [
        "bin/run_demo.py",
        "launch_experiment.sh",
        "src/main.py",
        "train.py",
    ]
    assert profile.configuration_candidates == [
        "configs/baseline.yaml",
        "pyproject.toml",
        "requirements_dev.txt",
    ]
    assert profile.declared_entrypoints == {"demo-train": "src.main:main"}
    assert {item.path for item in profile.top_level_entries} >= {"configs", "src", "train.py"}


def test_structure_profile_is_bounded_and_skips_generated_directories(tmp_path: Path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "main.py").write_text("ignored\n", encoding="utf-8")
    for index in range(3):
        (tmp_path / f"file_{index}.py").write_text("pass\n", encoding="utf-8")

    profile = build_repository_structure_profile(
        repository_root=tmp_path,
        source_id="src_repo",
        source_fingerprint="b" * 64,
        max_files=2,
    )

    assert profile.scanned_file_count == 2
    assert profile.scan_truncated is True
    assert "node_modules" not in {item.path for item in profile.top_level_entries}
