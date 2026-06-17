"""测试 InternalBenchmarkCase schema。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas import (
    BenchmarkDataset,
    BenchmarkEvaluationContract,
    BenchmarkMetric,
    BenchmarkSafety,
    BenchmarkRepository,
    BenchmarkReproducibility,
    InternalBenchmarkCase,
)


def _valid_case(**kw):
    defaults = dict(
        schema_version=1,
        case_id="internal_test_v1",
        scope="internal_benchmark_only",
        must_not_be_used_as_user_default=True,
        purpose="internal regression testing",
        baseline_name="PatchCore",
        implementation_name="patchcore-inspection",
        repository=BenchmarkRepository(
            url="https://github.com/example/patchcore",
            ref="v1.0",
            commit_sha="a" * 40,
            license="Apache-2.0",
            entrypoint_path="src/main.py",
            config_path="configs/default.yaml",
            dependency_files=["requirements.txt"],
        ),
        dataset=BenchmarkDataset(
            name="MVTec AD",
            category="bottle",
            root_env="AUTOAD_BENCHMARK_DATASET",
            license="CC BY-NC-SA 4.0",
            required_relative_paths=["bottle/train", "bottle/test"],
            manifest_strategy="relative_path_size_v1",
        ),
        fixed_parameters={"seed": 0, "backbone": "wide_resnet50_2"},
        evaluation=BenchmarkEvaluationContract(
            metrics=[
                BenchmarkMetric(name="image_AUROC", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.005),
            ],
            evaluator_paths=["eval.py"],
            protected_paths=["eval.py"],
            raw_result_paths=["results.json"],
            fingerprint_strategy="repo_commit_paths_and_config_v1",
        ),
        reproducibility=BenchmarkReproducibility(
            attempts=2, seed=0,
            require_same_repository_commit=True,
            require_same_case_config=True,
            require_same_dataset_manifest=True,
            require_same_evaluation_contract=True,
        ),
        safety=BenchmarkSafety(
            allow_network_during_execution=False,
            require_clean_repository=True,
            overwrite_existing_attempt=False,
            allow_paths_outside_workspace=False,
        ),
    )
    defaults.update(kw)
    return InternalBenchmarkCase(**defaults)


class TestInternalBenchmarkCase:
    def test_valid_minimal(self):
        case = _valid_case()
        assert case.case_id == "internal_test_v1"

    def test_dataset_acquisition_user_provided_valid(self):
        case = _valid_case(
            dataset=BenchmarkDataset(
                name="MVTec AD",
                category="bottle",
                root_env="AUTOAD_BENCHMARK_DATASET",
                license="CC BY-NC-SA 4.0",
                acquisition={
                    "mode": "user_provided",
                    "source_page": "https://www.mvtec.com/research-teaching/datasets/mvtec-ad",
                    "license": "CC-BY-NC-SA-4.0",
                    "redistribution_allowed": False,
                    "automatic_download": False,
                    "user_must_accept_license": True,
                },
                required_relative_paths=["bottle/train", "bottle/test"],
                manifest_strategy="relative_path_size_v1",
            )
        )

        assert case.dataset.acquisition is not None
        assert case.dataset.acquisition.mode == "user_provided"

    def test_scope_invalid_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(scope="user_task")

    def test_must_not_be_used_false_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(must_not_be_used_as_user_default=False)

    def test_invalid_case_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(case_id="../escape")

    def test_short_commit_sha_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(repository=BenchmarkRepository(
                url="https://github.com/example/patchcore", ref="v1", commit_sha="abc123",
                license="Apache-2.0", entrypoint_path="x.py", dependency_files=["x.txt"],
            ))

    def test_all_zero_commit_rejected(self):
        with pytest.raises(ValidationError, match="all zeros"):
            _valid_case(repository=BenchmarkRepository(
                url="https://github.com/example/patchcore", ref="v1", commit_sha="0" * 40,
                license="Apache-2.0", entrypoint_path="x.py", dependency_files=["x.txt"],
            ))

    def test_todo_in_url_rejected(self):
        with pytest.raises(ValidationError, match="placeholder"):
            _valid_case(repository=BenchmarkRepository(
                url="https://TODO.example.com", ref="v1", commit_sha="a" * 40,
                license="Apache-2.0", entrypoint_path="x.py", dependency_files=["x.txt"],
            ))

    def test_root_env_format_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(dataset=BenchmarkDataset(
                name="MVTec AD", category="bottle", root_env="not_uppercase",
                license="CC", required_relative_paths=["x"], manifest_strategy="relative_path_size_v1",
            ))

    def test_attempts_not_two_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(reproducibility=BenchmarkReproducibility(
                attempts=3, seed=0,
                require_same_repository_commit=True, require_same_case_config=True,
                require_same_dataset_manifest=True, require_same_evaluation_contract=True,
            ))

    def test_no_required_metric_rejected(self):
        with pytest.raises(ValidationError, match="required metric"):
            _valid_case(evaluation=BenchmarkEvaluationContract(
                metrics=[BenchmarkMetric(name="image_AUROC", required=False, direction="maximize", unit="ratio", absolute_tolerance=0.005)],
                evaluator_paths=["eval.py"], protected_paths=["eval.py"],
                raw_result_paths=["r.json"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ))

    def test_duplicate_metric_name_rejected(self):
        with pytest.raises(ValidationError, match="duplicate"):
            _valid_case(evaluation=BenchmarkEvaluationContract(
                metrics=[
                    BenchmarkMetric(name="image_AUROC", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.005),
                    BenchmarkMetric(name="image_auroc", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.005),
                ],
                evaluator_paths=["eval.py"], protected_paths=["eval.py"],
                raw_result_paths=["r.json"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ))

    def test_evaluator_not_in_protected_rejected(self):
        with pytest.raises(ValidationError, match="subset"):
            _valid_case(evaluation=BenchmarkEvaluationContract(
                metrics=[BenchmarkMetric(name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0)],
                evaluator_paths=["eval.py"],
                protected_paths=["other.py"],
                raw_result_paths=["r.json"], fingerprint_strategy="repo_commit_paths_and_config_v1",
            ))

    def test_absolute_path_rejected(self):
        with pytest.raises(ValidationError, match="relative"):
            _valid_case(repository=BenchmarkRepository(
                url="https://github.com/example/patchcore", ref="v1", commit_sha="a" * 40,
                license="Apache-2.0", entrypoint_path="/absolute/path.py", dependency_files=["x.txt"],
            ))

    def test_dotdot_path_rejected(self):
        with pytest.raises(ValidationError, match=".."):
            _valid_case(repository=BenchmarkRepository(
                url="https://github.com/example/patchcore", ref="v1", commit_sha="a" * 40,
                license="Apache-2.0", entrypoint_path="../escape.py", dependency_files=["x.txt"],
            ))

    def test_raw_result_absolute_path_rejected(self):
        with pytest.raises(ValidationError):
            _valid_case(evaluation=BenchmarkEvaluationContract(
                metrics=[BenchmarkMetric(name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0)],
                evaluator_paths=["e.py"], protected_paths=["e.py"],
                raw_result_paths=["/absolute/results.json"],
                fingerprint_strategy="repo_commit_paths_and_config_v1",
            ))

    def test_raw_result_dotdot_path_rejected(self):
        with pytest.raises(ValidationError, match=".."):
            _valid_case(evaluation=BenchmarkEvaluationContract(
                metrics=[BenchmarkMetric(name="m", required=True, direction="maximize", unit="ratio", absolute_tolerance=0.0)],
                evaluator_paths=["e.py"], protected_paths=["e.py"],
                raw_result_paths=["../../results.json"],
                fingerprint_strategy="repo_commit_paths_and_config_v1",
            ))

    def test_nan_in_fixed_parameters_rejected(self):
        import math
        with pytest.raises(ValidationError, match="finite"):
            _valid_case(fixed_parameters={"ratio": float("nan")})

    def test_infinity_in_fixed_parameters_rejected(self):
        import math
        with pytest.raises(ValidationError, match="finite"):
            _valid_case(fixed_parameters={"ratio": float("inf")})

    def test_changeme_in_baseline_name_rejected(self):
        with pytest.raises(ValidationError, match="placeholder"):
            _valid_case(baseline_name="CHANGEME")
