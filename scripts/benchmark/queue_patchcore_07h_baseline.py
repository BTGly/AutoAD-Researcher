#!/usr/bin/env python3
"""Queue the approved 07H seed-0 baseline through ExperimentAttempt/Worker."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.config import compute_case_sha256, load_internal_benchmark_case  # noqa: E402
from autoad_researcher.benchmarks.hashing import sha256_file  # noqa: E402
from autoad_researcher.benchmarks.patchcore_smoke import build_patchcore_smoke_command_plan, patchcore_smoke_metric_specs  # noqa: E402
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService  # noqa: E402
from autoad_researcher.experiment.evaluation_contract import (  # noqa: E402
    EvaluationContract, EvaluationContractStore, EvaluationMetric, EvaluationResourceBudget, freeze_protected_artifacts,
)
from autoad_researcher.experiment.finalizer import ProtectedArtifactHashes  # noqa: E402
from autoad_researcher.experiment.scientific_assessment import ScientificAssessmentInputsStore, ScientificEvaluationInputs  # noqa: E402
from autoad_researcher.experiment.session_store import ExperimentSessionStore  # noqa: E402
from autoad_researcher.experiment.validity import ComparisonIdentity  # noqa: E402
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs, experiment_command_sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="07h")
    parser.add_argument("--seed", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--case", default="configs/benchmarks/internal_patchcore_mvtec_bottle_smoke_v1.yaml")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--benchmark-python", required=True)
    parser.add_argument("--weight", default="workspace/cache/torch_probe/hub/checkpoints/wide_resnet50_2-95faca4d.pth")
    parser.add_argument("--lockfile", default="configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt")
    args = parser.parse_args()
    run_dir = PROJECT_ROOT / "runs" / args.run_id
    readiness = _load(run_dir / "artifacts" / "07h" / "physical_readiness.json")
    if readiness.get("status") != "ready":
        raise SystemExit("PhysicalReadinessGate is not ready")
    case_path, repo, python, weight, lockfile = (_path(args.case), _path(args.repo), _path(args.benchmark_python), _path(args.weight), _path(args.lockfile))
    case = load_internal_benchmark_case(case_path)
    case_sha = compute_case_sha256(case)
    if readiness.get("case_sha256") != case_sha:
        raise SystemExit("ready evidence does not match the frozen smoke case")
    b_dev_sha = str(readiness["dataset"]["b_dev_manifest_sha256"])
    protected_root = run_dir / "artifacts" / "07h" / "protected_source"
    for relative in case.evaluation.protected_paths:
        target = protected_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(repo / relative, target)
    protected_paths = [str((protected_root / relative).relative_to(run_dir)) for relative in case.evaluation.protected_paths]
    session_store = ExperimentSessionStore()
    session, _ = session_store.create_or_get(run_dir, task_ref="artifacts/07h/physical_readiness.json", task_hash=case_sha, execution_mode="agent_assisted_after_approval", repository_ref=str(repo))
    snapshot = run_dir / "artifacts" / "07h" / "baseline_environment_snapshot.json"
    _write(snapshot, {"repository": str(repo), "repository_commit": readiness["environment"]["repository_commit"], "benchmark_python": str(python), "lockfile_sha256": sha256_file(lockfile), "weight_sha256": sha256_file(weight), "case_sha256": case_sha})
    session_store.update_environment_state(run_dir, session_id=session.session_id, status="READY_FOR_BASELINE", environment_status="ready", readiness_status="ready", readiness_blockers=[], repository_ref=str(repo), environment_snapshot_ref=str(snapshot.relative_to(run_dir)))
    contract = EvaluationContract(
        contract_id="evaluation_contract_000001", session_id=session.session_id, revision=0,
        baseline_commit=case.repository.commit_sha, dataset_identity=f"07h-b-dev:{b_dev_sha}", split_identity=b_dev_sha,
        b_dev_ref="artifacts/07h/dataset/b_dev_manifest.json", b_test_ref="artifacts/07h/dataset/b_test_manifest.json",
        category_set=[case.dataset.category], metrics=[EvaluationMetric(name=m.name, direction=m.direction, implementation_ref=case.evaluation.evaluator_paths[0]) for m in case.evaluation.metrics],
        primary_metric=case.evaluation.metrics[0].name, aggregation="mean", seeds=[0], checkpoint_selection="not_applicable",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=1800, max_gpu_seconds=1800), protected_paths=protected_paths,
    )
    frozen = EvaluationContractStore().freeze(run_dir, contract=contract)
    session_store.bind_evaluation_contract(run_dir, session_id=session.session_id, evaluation_contract_ref=frozen.ref, evaluation_contract_sha256=frozen.sha256, evaluation_contract_revision=0)
    protected = run_dir / "artifacts" / "07h" / "protected_hashes.json"
    _write(protected, ProtectedArtifactHashes(hashes=freeze_protected_artifacts(run_dir, protected_paths)).model_dump(mode="json"))
    seeded_parameters = {**case.fixed_parameters, "seed": args.seed}
    seeded_case = case.model_copy(update={"fixed_parameters": seeded_parameters})
    command_file = run_dir / "artifacts" / "07h" / f"baseline_seed_{args.seed}_patchcore_command.json"
    smoke = build_patchcore_smoke_command_plan(case=seeded_case, run_id=args.run_id, attempt=f"baseline_seed_{args.seed}", dataset_path=str(run_dir / "data" / "b_dev"))
    _write(command_file, {"command_id": smoke.command_id, "argv": [str(repo / case.repository.entrypoint_path), *smoke.args[1:]], "results_path": patchcore_smoke_metric_specs(case)[0].source_path, "metrics": [{"name": metric.name, "required": metric.required} for metric in case.evaluation.metrics], "protected_paths": case.evaluation.protected_paths})
    plan = _plan(command_file, repo, python, weight, seeded_case)
    refs = ExperimentInputRefs(repository_fingerprint=case.repository.commit_sha, environment_sha256=sha256_file(lockfile), dataset_manifest_sha256=b_dev_sha, asset_manifest_sha256=sha256_file(weight), command_sha256=experiment_command_sha256(plan))
    identity = ComparisonIdentity(dataset_identity=contract.dataset_identity, split_identity=contract.split_identity, seed=args.seed, checkpoint_selection=contract.checkpoint_selection, command_sha256=refs.command_sha256, metric_implementation_refs=case.evaluation.evaluator_paths, evaluation_contract_sha256=frozen.sha256, outputs_complete=True)
    started = ExperimentAttemptService().create_or_get_attempt(run_dir, session_id=session.session_id, job_type="experiment_baseline", idempotency_key=f"07h:baseline:seed:{args.seed}:{refs.command_sha256}", command_plan=plan, input_refs=refs, job_timeout_sec=1800, required_device_count=1, required_vram_mb=20000, evaluation_contract_ref=frozen.ref, evaluation_contract_sha256=frozen.sha256, protected_artifact_report_ref=str(protected.relative_to(run_dir)), protected_artifact_report_sha256=sha256_file(protected))
    ScientificAssessmentInputsStore().save(run_dir / "attempts" / started.attempt.attempt_id, ScientificEvaluationInputs(baseline_metrics={}, candidate_identity=identity, baseline_identity=identity))
    print(json.dumps({"attempt_id": started.attempt.attempt_id, "job_id": started.pipeline_job["job_id"], "disposition": started.disposition}, ensure_ascii=False))
    return 0


def _plan(command_file: Path, repo: Path, python: Path, weight: Path, case) -> ExperimentCommandPlan:
    expected_csv = patchcore_smoke_metric_specs(case)[0].source_path
    source = PROJECT_ROOT / "src"
    command_sha = sha256_file(command_file)
    return ExperimentCommandPlan(schema_version=1, command_id=f"baseline_seed_0_{case.case_id}", program=str(python), args=["-m", "autoad_researcher.benchmarks.patchcore_07h_runner", "--command-file", str(command_file), "--repository", str(repo), "--command-sha256", command_sha], cwd="attempts", environment={"PYTHONPATH": f"{source}:{repo / 'src'}", "TORCH_HOME": str(weight.parent.parent.parent), "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "0", "AUTOAD_PATCHCORE_COMMAND_SHA256": command_sha}, timeout_seconds=1800, network=False, expected_outputs=[expected_csv, "metrics.json", "parsed_metrics.json", "protected_hash_before.json", "protected_hash_after.json", "command.json"])


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
