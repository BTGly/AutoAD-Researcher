from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.repository_intelligence.workload_target import (
    RepositoryWorkloadTarget,
    analyze_repository_workload_target,
)


def test_reads_unique_exact_level_and_problem_file(tmp_path: Path):
    repository = tmp_path / "KernelBench"
    task = repository / "KernelBench" / "level2" / "40_Matmul.py"
    task.parent.mkdir(parents=True)
    task.write_text(
        "import torch\n\nclass Model(torch.nn.Module):\n    pass\n",
        encoding="utf-8",
    )
    (repository / "KernelBench" / "level2" / "41_Other.py").write_text(
        "class DifferentTask: pass\n",
        encoding="utf-8",
    )
    output = tmp_path / "analysis.json"

    result = analyze_repository_workload_target(
        repository_root=repository,
        target=RepositoryWorkloadTarget(level=2, problem_id=40),
        output_path=output,
    )

    assert result.status == "found"
    assert result.resolved_path == "KernelBench/level2/40_Matmul.py"
    assert "class Model" in result.content_preview
    assert result.bytes_read == task.stat().st_size
    assert result.file_sha256
    assert result.content_sha256
    assert json.loads(output.read_text(encoding="utf-8"))["resolved_path"] == result.resolved_path


def test_refuses_to_select_when_exact_target_is_ambiguous(tmp_path: Path):
    repository = tmp_path / "repo"
    first = repository / "level2" / "40_First.py"
    second = repository / "examples" / "level2" / "40_Second.py"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")

    result = analyze_repository_workload_target(
        repository_root=repository,
        target=RepositoryWorkloadTarget(level=2, problem_id=40),
        output_path=tmp_path / "analysis.json",
    )

    assert result.status == "ambiguous"
    assert result.resolved_path is None
    assert result.bytes_read == 0
    assert set(result.candidate_paths) == {
        "level2/40_First.py",
        "examples/level2/40_Second.py",
    }
