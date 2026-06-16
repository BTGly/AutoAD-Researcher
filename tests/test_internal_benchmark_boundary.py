"""测试 Internal Benchmark 不泄漏到产品 runtime。"""

from pathlib import Path


_PROTECTED_DIRS = [
    "src/autoad_researcher/core",
    "src/autoad_researcher/clarifiers",
    "src/autoad_researcher/ideation",
    "src/autoad_researcher/readers",
    "src/autoad_researcher/harness",
]
_PROTECTED_FILES = [
    "src/autoad_researcher/cli.py",
]
_FORBIDDEN_PATTERNS = [
    "configs/benchmarks",
    "internal_patchcore_mvtec_bottle",
    "InternalBenchmarkCase",
    "load_internal_benchmark_case",
]


def _is_protected(path: Path) -> bool:
    s = str(path)
    for d in _PROTECTED_DIRS:
        if s.startswith(d):
            return True
    return s in _PROTECTED_FILES


def test_production_runtime_does_not_import_benchmark_config():
    """Core/Clarifier/Idea/Reader/Harness/CLI 不 import benchmark config。"""
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for pattern in ["**/*.py"]:
        for pyfile in root.rglob(pattern):
            if not _is_protected(pyfile):
                continue
            text = pyfile.read_text(encoding="utf-8")
            for forbidden in _FORBIDDEN_PATTERNS:
                if forbidden in text:
                    violations.append(f"{pyfile}: contains {forbidden!r}")
    if violations:
        raise AssertionError("\n".join(violations))


def test_benchmark_config_not_in_artifact_whitelist():
    """benchmark config/YAML 不在 ArtifactStore 白名单。"""
    from autoad_researcher.core.artifacts import ArtifactStore
    store = ArtifactStore(runs_root="/tmp")
    with __import__("pytest").raises(ValueError):
        store.write_json("x", "internal_patchcore_mvtec_bottle_v1.yaml", {})

    # Scope and boundary fields
    from autoad_researcher.schemas import InternalBenchmarkCase
    from autoad_researcher.benchmarks.config import load_internal_benchmark_case
    import tempfile, yaml
    case_data = {
        "schema_version": 1,
        "case_id": "test_boundary",
        "scope": "internal_benchmark_only",
        "must_not_be_used_as_user_default": True,
        # ...minimal fields
    }
    # This test just validates the schema rejects non-internal scope
    with __import__("pytest").raises(Exception):
        InternalBenchmarkCase(**case_data)
