from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from autoad_researcher.benchmarks.patchcore_two_cycle import (
    PatchCoreTwoCycleInputs,
    PatchCoreTwoCycleReadinessChecker,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()


def _fixture(tmp_path: Path) -> PatchCoreTwoCycleInputs:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "model.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "eval.py").write_text("protected = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")

    dataset = tmp_path / "dataset"
    for relative in ("bottle/train/good", "bottle/test", "bottle/ground_truth"):
        (dataset / relative).mkdir(parents=True)
    lockfile = tmp_path / "requirements.lock.txt"
    lockfile.write_text("fixture==1\n", encoding="utf-8")
    weight = tmp_path / "weight.pth"
    weight.write_bytes(b"fixture-weight")
    patch_1 = tmp_path / "cycle_1.patch"
    patch_1.write_text(
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n"
        "+++ b/model.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 2\n",
        encoding="utf-8",
    )
    patch_2 = tmp_path / "cycle_2.patch"
    patch_2.write_text(
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n"
        "+++ b/model.py\n"
        "@@ -1 +1 @@\n"
        "-value = 2\n"
        "+value = 3\n",
        encoding="utf-8",
    )
    return PatchCoreTwoCycleInputs(
        repository_path=repo,
        expected_repository_commit=_git(repo, "rev-parse", "HEAD"),
        dataset_root=dataset,
        required_dataset_paths=["bottle/train/good", "bottle/test", "bottle/ground_truth"],
        benchmark_python=Path(sys.executable),
        lockfile_path=lockfile,
        weight_path=weight,
        expected_weight_sha256=hashlib.sha256(weight.read_bytes()).hexdigest(),
        protected_paths=["eval.py"],
        cycle_1_patch=patch_1,
        cycle_2_patch=patch_2,
    )


def test_real_rehearsal_gate_accepts_clean_sequential_nonprotected_patches(tmp_path: Path):
    inputs = _fixture(tmp_path)
    result = PatchCoreTwoCycleReadinessChecker().check(inputs)
    assert result.status == "ready"
    assert result.blockers == []
    assert [item.changed_paths for item in result.cycle_evidence] == [["model.py"], ["model.py"]]
    assert [item.cycle for item in result.cycle_evidence] == [1, 2]
    assert (inputs.repository_path / "model.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not _git(inputs.repository_path, "status", "--porcelain")


def test_real_rehearsal_gate_blocks_protected_edits_and_missing_prerequisites(tmp_path: Path):
    inputs = _fixture(tmp_path)
    inputs.cycle_1_patch.write_text(
        "diff --git a/eval.py b/eval.py\n"
        "--- a/eval.py\n"
        "+++ b/eval.py\n"
        "@@ -1 +1 @@\n"
        "-protected = True\n"
        "+protected = False\n",
        encoding="utf-8",
    )
    inputs.weight_path.unlink()
    result = PatchCoreTwoCycleReadinessChecker().check(inputs)
    assert result.status == "blocked"
    assert "offline PatchCore backbone weight is missing" in result.blockers

    inputs.weight_path.write_bytes(b"fixture-weight")
    protected = PatchCoreTwoCycleReadinessChecker().check(inputs)
    assert protected.status == "blocked"
    assert any("protected paths" in blocker for blocker in protected.blockers)


def test_real_rehearsal_gate_blocks_second_patch_that_does_not_follow_first(tmp_path: Path):
    inputs = _fixture(tmp_path)
    inputs.cycle_2_patch.write_text(
        "diff --git a/model.py b/model.py\n"
        "--- a/model.py\n"
        "+++ b/model.py\n"
        "@@ -1 +1 @@\n"
        "-value = 1\n"
        "+value = 3\n",
        encoding="utf-8",
    )
    result = PatchCoreTwoCycleReadinessChecker().check(inputs)
    assert result.status == "blocked"
    assert any("does not apply after prior cycles" in blocker for blocker in result.blockers)
