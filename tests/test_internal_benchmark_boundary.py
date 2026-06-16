"""测试 Internal Benchmark 不泄漏到产品 runtime。"""

from pathlib import Path
import pytest


_PROTECTED_DIRS = [
    "src/autoad_researcher/core",
    "src/autoad_researcher/clarifiers",
    "src/autoad_researcher/ideation",
    "src/autoad_researcher/readers",
    "src/autoad_researcher/harness",
]
_PROTECTED_FILES = [
    "src/autoad_researcher/__main__.py",
    "src/autoad_researcher/cli.py",
]
_FORBIDDEN_PATTERNS = [
    "configs/benchmarks",
    "internal_patchcore_mvtec_bottle",
    "InternalBenchmarkCase",
    "load_internal_benchmark_case",
]


def _is_protected(path: Path, *, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    return any(
        rel == d or rel.startswith(f"{d}/") for d in _PROTECTED_DIRS
    ) or rel in _PROTECTED_FILES


class TestIsProtected:
    def test_core_is_protected(self):
        root = Path(__file__).resolve().parents[1]
        assert _is_protected(root / "src/autoad_researcher/core/x.py", root=root)
        assert _is_protected(root / "src/autoad_researcher/harness/y.py", root=root)
        assert _is_protected(root / "src/autoad_researcher/cli.py", root=root)
        assert _is_protected(root / "src/autoad_researcher/__main__.py", root=root)

    def test_schemas_is_not_protected(self):
        root = Path(__file__).resolve().parents[1]
        assert not _is_protected(root / "src/autoad_researcher/schemas/benchmark.py", root=root)
        assert not _is_protected(root / "scripts/benchmark/x.py", root=root)


def test_production_runtime_does_not_import_benchmark_config():
    """Core/Clarifier/Idea/Reader/Harness/CLI 不 import benchmark config。"""
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for pyfile in root.rglob("*.py"):
        if not _is_protected(pyfile, root=root):
            continue
        text = pyfile.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_PATTERNS:
            if forbidden in text:
                violations.append(f"{pyfile}: contains {forbidden!r}")
    if violations:
        raise AssertionError("\n".join(violations))


def test_benchmark_config_not_in_artifact_whitelist():
    from autoad_researcher.core.artifacts import ArtifactStore
    store = ArtifactStore(runs_root="/tmp")
    with pytest.raises(ValueError):
        store.write_json("x", "internal_patchcore_mvtec_bottle_v1.yaml", {})


def test_scope_boundary_requires_internal_only():
    from autoad_researcher.schemas import (
        BenchmarkDataset, BenchmarkEvaluationContract, BenchmarkMetric,
        BenchmarkReproducibility, BenchmarkRepository, BenchmarkSafety,
        InternalBenchmarkCase,
    )
    with pytest.raises(Exception):
        InternalBenchmarkCase(
            schema_version=1, case_id="t",
            scope="user_task",  # type: ignore[arg-type]
            must_not_be_used_as_user_default=True, purpose="x",
            baseline_name="B", implementation_name="I",
            repository=BenchmarkRepository(
                url="https://x", ref="v1", commit_sha="a" * 40, license="L",
                entrypoint_path="m.py", dependency_files=["r.txt"],
            ),
            dataset=BenchmarkDataset(
                name="D", category="c", root_env="E", license="L",
                required_relative_paths=["x"], manifest_strategy="relative_path_size_v1",
            ),
            fixed_parameters={},
            evaluation=BenchmarkEvaluationContract(
                metrics=[BenchmarkMetric(name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0)],
                evaluator_paths=["e.py"], protected_paths=["e.py"],
                raw_result_paths=["r"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ),
            reproducibility=BenchmarkReproducibility(
                attempts=2, seed=0, require_same_repository_commit=True,
                require_same_case_config=True, require_same_dataset_manifest=True,
                require_same_evaluation_contract=True,
            ),
            safety=BenchmarkSafety(
                allow_network_during_execution=False, require_clean_repository=True,
                overwrite_existing_attempt=False, allow_paths_outside_workspace=False,
            ),
        )
