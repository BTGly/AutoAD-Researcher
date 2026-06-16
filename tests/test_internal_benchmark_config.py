"""测试 benchmark config loader。"""

import json
import tempfile
from pathlib import Path

from autoad_researcher.benchmarks.config import (
    canonical_case_json,
    compute_case_sha256,
    load_internal_benchmark_case,
)
from autoad_researcher.schemas import InternalBenchmarkCase


def _save_temp_yaml(case: InternalBenchmarkCase) -> Path:
    import yaml
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8")
    yaml.safe_dump(case.model_dump(mode="json"), tmp, allow_unicode=True, sort_keys=False)
    tmp.close()
    return Path(tmp.name)


class TestBenchmarkConfigLoader:
    def test_roundtrip(self):
        case = InternalBenchmarkCase(
            schema_version=1,
            case_id="internal_test_v1",
            scope="internal_benchmark_only",
            must_not_be_used_as_user_default=True,
            purpose="test",
            baseline_name="PatchCore",
            implementation_name="patchcore-inspection",
            repository=__import__("autoad_researcher.schemas", fromlist=["BenchmarkRepository"]).BenchmarkRepository(
                url="https://github.com/example/patchcore", ref="v1", commit_sha="a" * 40,
                license="Apache-2.0", entrypoint_path="main.py", dependency_files=["req.txt"],
            ),
            dataset=__import__("autoad_researcher.schemas", fromlist=["BenchmarkDataset"]).BenchmarkDataset(
                name="MVTec AD", category="bottle", root_env="AUTOAD_DATASET",
                license="CC", required_relative_paths=["x"], manifest_strategy="relative_path_size_v1",
            ),
            fixed_parameters={"seed": 0},
            evaluation=__import__("autoad_researcher.schemas", fromlist=["BenchmarkEvaluationContract"]).BenchmarkEvaluationContract(
                metrics=[__import__("autoad_researcher.schemas", fromlist=["BenchmarkMetric"]).BenchmarkMetric(
                    name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0,
                )],
                evaluator_paths=["e.py"], protected_paths=["e.py"],
                raw_result_paths=["r.json"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ),
            reproducibility=__import__("autoad_researcher.schemas", fromlist=["BenchmarkReproducibility"]).BenchmarkReproducibility(
                attempts=2, seed=0,
                require_same_repository_commit=True, require_same_case_config=True,
                require_same_dataset_manifest=True, require_same_evaluation_contract=True,
            ),
            safety=__import__("autoad_researcher.schemas", fromlist=["BenchmarkSafety"]).BenchmarkSafety(
                allow_network_during_execution=False, require_clean_repository=True,
                overwrite_existing_attempt=False, allow_paths_outside_workspace=False,
            ),
        )
        path = _save_temp_yaml(case)
        loaded = load_internal_benchmark_case(path)
        assert loaded.case_id == "internal_test_v1"
        assert loaded.repository.commit_sha == "a" * 40
        path.unlink()

    def test_not_mapping_rejected(self):
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8")
        tmp.write("- not a mapping\n")
        tmp.close()
        with __import__("pytest").raises(ValueError, match="mapping"):
            load_internal_benchmark_case(tmp.name)
        Path(tmp.name).unlink()

    def test_canonical_json_deterministic(self):
        from autoad_researcher.schemas import InternalBenchmarkCase
        case = InternalBenchmarkCase(
            schema_version=1, case_id="t", scope="internal_benchmark_only",
            must_not_be_used_as_user_default=True, purpose="x", baseline_name="B",
            implementation_name="I",
            repository=__import__("autoad_researcher.schemas", fromlist=["BenchmarkRepository"]).BenchmarkRepository(
                url="https://x", ref="v1", commit_sha="a" * 40, license="L",
                entrypoint_path="m.py", dependency_files=["r.txt"],
            ),
            dataset=__import__("autoad_researcher.schemas", fromlist=["BenchmarkDataset"]).BenchmarkDataset(
                name="D", category="c", root_env="E", license="L",
                required_relative_paths=["x"], manifest_strategy="relative_path_size_v1",
            ),
            fixed_parameters={},
            evaluation=__import__("autoad_researcher.schemas", fromlist=["BenchmarkEvaluationContract"]).BenchmarkEvaluationContract(
                metrics=[__import__("autoad_researcher.schemas", fromlist=["BenchmarkMetric"]).BenchmarkMetric(
                    name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0,
                )],
                evaluator_paths=["e.py"], protected_paths=["e.py"],
                raw_result_paths=["r"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ),
            reproducibility=__import__("autoad_researcher.schemas", fromlist=["BenchmarkReproducibility"]).BenchmarkReproducibility(
                attempts=2, seed=0, require_same_repository_commit=True,
                require_same_case_config=True, require_same_dataset_manifest=True,
                require_same_evaluation_contract=True,
            ),
            safety=__import__("autoad_researcher.schemas", fromlist=["BenchmarkSafety"]).BenchmarkSafety(
                allow_network_during_execution=False, require_clean_repository=True,
                overwrite_existing_attempt=False, allow_paths_outside_workspace=False,
            ),
        )
        # YAML key order should not affect SHA
        sha1 = compute_case_sha256(case)
        json1 = canonical_case_json(case)
        # Re-parse to confirm determinism
        case2 = InternalBenchmarkCase.model_validate(json.loads(json1))
        sha2 = compute_case_sha256(case2)
        assert sha1 == sha2

    def test_field_change_changes_sha(self):
        from autoad_researcher.schemas import InternalBenchmarkCase
        case = InternalBenchmarkCase(
            schema_version=1, case_id="t", scope="internal_benchmark_only",
            must_not_be_used_as_user_default=True, purpose="x", baseline_name="B",
            implementation_name="I",
            repository=__import__("autoad_researcher.schemas", fromlist=["BenchmarkRepository"]).BenchmarkRepository(
                url="https://x", ref="v1", commit_sha="a" * 40, license="L",
                entrypoint_path="m.py", dependency_files=["r.txt"],
            ),
            dataset=__import__("autoad_researcher.schemas", fromlist=["BenchmarkDataset"]).BenchmarkDataset(
                name="D", category="c", root_env="E", license="L",
                required_relative_paths=["x"], manifest_strategy="relative_path_size_v1",
            ),
            fixed_parameters={},
            evaluation=__import__("autoad_researcher.schemas", fromlist=["BenchmarkEvaluationContract"]).BenchmarkEvaluationContract(
                metrics=[__import__("autoad_researcher.schemas", fromlist=["BenchmarkMetric"]).BenchmarkMetric(
                    name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0,
                )],
                evaluator_paths=["e.py"], protected_paths=["e.py"],
                raw_result_paths=["r"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ),
            reproducibility=__import__("autoad_researcher.schemas", fromlist=["BenchmarkReproducibility"]).BenchmarkReproducibility(
                attempts=2, seed=0, require_same_repository_commit=True,
                require_same_case_config=True, require_same_dataset_manifest=True,
                require_same_evaluation_contract=True,
            ),
            safety=__import__("autoad_researcher.schemas", fromlist=["BenchmarkSafety"]).BenchmarkSafety(
                allow_network_during_execution=False, require_clean_repository=True,
                overwrite_existing_attempt=False, allow_paths_outside_workspace=False,
            ),
        )
        sha1 = compute_case_sha256(case)
        case2 = case.model_copy(update={"case_id": "changed"})
        assert compute_case_sha256(case2) != sha1
