from __future__ import annotations

from pathlib import Path

from autoad_researcher.benchmarks.config import load_internal_benchmark_case
from autoad_researcher.benchmarks.patchcore_07h_readiness import PhysicalReadinessGate, PhysicalReadinessInputs


def test_readiness_fails_closed_and_writes_machine_independent_report(tmp_path: Path):
    project = Path(__file__).resolve().parents[1]
    case = load_internal_benchmark_case(project / "configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml")
    report = PhysicalReadinessGate().check(PhysicalReadinessInputs(
        case=case,
        source_root=tmp_path / "missing-source",
        run_dir=tmp_path / "run",
        repository_path=tmp_path / "missing-repo",
        benchmark_python=tmp_path / "missing-python",
        lockfile_path=tmp_path / "missing.lock",
        environment_spec_path=tmp_path / "missing.yaml",
        weight_path=tmp_path / "missing.pth",
        required_free_vram_mb=10000,
        maximum_used_vram_mb=100,
    ))

    assert report["status"] == "blocked"
    assert report["blockers"]
    rendered = (tmp_path / "run/artifacts/07h/physical_readiness.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in rendered
