#!/usr/bin/env python3
"""Freeze 07H retrospective seed calibration without rewriting baseline evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from autoad_researcher.benchmarks.hashing import sha256_file  # noqa: E402
from autoad_researcher.experiment.evaluation_contract import (  # noqa: E402
    EvaluationContract,
    EvaluationContractStore,
    EvaluationSeedPolicy,
)
from autoad_researcher.experiment.noise_calibration import (  # noqa: E402
    NoiseCalibrationProtocol,
    NoiseCalibrationProtocolStore,
    new_protocol_created_at,
)
from autoad_researcher.experiment.noise_floor import calibrate_noise_floor  # noqa: E402
from autoad_researcher.experiment.scientific_assessment import ScientificAssessmentService  # noqa: E402
from autoad_researcher.experiment.session_store import ExperimentSessionStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="07h")
    parser.add_argument("--included-attempt", action="append", required=True)
    parser.add_argument("--excluded-attempt", action="append", default=[])
    parser.add_argument("--protocol-id", default="noise_calibration_000001")
    args = parser.parse_args()

    run_dir = PROJECT_ROOT / "runs" / args.run_id
    session = _single_session(run_dir)
    base_ref, base_sha, base = _base_contract(run_dir, session)
    if base.revision != 0 or base.schema_version != 1 or base.seeds != [0]:
        raise SystemExit("07H retrospective calibration requires the preserved revision-0 seed-0 contract")

    evidence = [_validate_attempt(run_dir, attempt_id, base_ref=base_ref, base_sha=base_sha) for attempt_id in args.included_attempt]
    if len(evidence) != 3:
        raise SystemExit("07H retrospective calibration requires exactly three included Attempts")
    seeds = [item["seed"] for item in evidence]
    if sorted(seeds) != [0, 1, 2]:
        raise SystemExit("included Attempts must provide exactly seeds 0, 1, and 2")
    _validate_invariants(evidence)

    protocol_store = NoiseCalibrationProtocolStore()
    try:
        protocol = protocol_store.load(run_dir, session_id=session.session_id, protocol_id=args.protocol_id)
    except FileNotFoundError:
        protocol = NoiseCalibrationProtocol(
            protocol_id=args.protocol_id,
            session_id=session.session_id,
            base_evaluation_contract_ref=base_ref,
            base_evaluation_contract_sha256=base_sha,
            allowed_seed_set=[0, 1, 2],
            invariant_fields=[
                "repository_fingerprint",
                "environment_sha256",
                "dataset_manifest_sha256",
                "asset_manifest_sha256",
                "evaluation_contract_ref",
                "evaluation_contract_sha256",
                "protected_hashes",
            ],
            variable_fields=["seed", "PYTHONHASHSEED", "command_id seed component"],
            included_attempts=args.included_attempt,
            excluded_attempts=args.excluded_attempt,
            retrospective_or_prospective="retrospective",
            created_at=new_protocol_created_at(),
        )
    protocol_ref = protocol_store.freeze(run_dir, protocol=protocol)
    protocol_path = run_dir / protocol_ref
    if protocol_path.is_file():
        protocol = NoiseCalibrationProtocol.model_validate_json(protocol_path.read_text(encoding="utf-8"))

    metrics = _retrospective_noise_floors(session.session_id, base.category_set[0], evidence, updated_at=protocol.created_at)
    summary = {
        "schema_version": 1,
        "status": "PROVISIONAL_RETROSPECTIVE",
        "promotion_eligible": False,
        "reason": "multi-seed calibration protocol was frozen after immutable baseline Attempts completed",
        "noise_calibration_protocol_ref": protocol_ref,
        "noise_calibration_protocol_sha256": sha256_file(protocol_path),
        "base_evaluation_contract_ref": base_ref,
        "base_evaluation_contract_sha256": base_sha,
        "valid_attempt_ids": args.included_attempt,
        "excluded_attempt_ids": args.excluded_attempt,
        "noise_floors": metrics,
    }
    _write_immutable(run_dir / "artifacts" / "07h" / "retrospective_noise_calibration.json", summary)

    v2 = base.model_copy(
        update={
            "schema_version": 2,
            "contract_id": "evaluation_contract_000002",
            "revision": 1,
            "seeds": [0, 1, 2],
            "seed_policy": EvaluationSeedPolicy(
                baseline_calibration_seeds=[0, 1, 2],
                exploration_seed=0,
                confirmation_seed_policy="explicit",
            ),
        }
    )
    frozen = EvaluationContractStore().freeze(run_dir, contract=v2)
    ExperimentSessionStore().bind_evaluation_contract(
        run_dir,
        session_id=session.session_id,
        evaluation_contract_ref=frozen.ref,
        evaluation_contract_sha256=frozen.sha256,
        evaluation_contract_revision=frozen.contract.revision,
    )
    for attempt_id in args.included_attempt:
        ScientificAssessmentService().effective_assessment(run_dir, attempt_id=attempt_id)
    print(json.dumps({"protocol_ref": protocol_ref, "evaluation_contract_v2_ref": frozen.ref, "summary_ref": "artifacts/07h/retrospective_noise_calibration.json"}, ensure_ascii=False))
    return 0


def _single_session(run_dir: Path):
    sessions = sorted((run_dir / "experiments" / "sessions").glob("session_*.json"))
    if len(sessions) != 1:
        raise ValueError("07H calibration requires exactly one session artifact")
    from autoad_researcher.experiment.session_store import ExperimentSession

    return ExperimentSession.model_validate_json(sessions[0].read_text(encoding="utf-8"))


def _base_contract(run_dir: Path, session) -> tuple[str, str, EvaluationContract]:
    if not session.evaluation_contract_ref or not session.evaluation_contract_sha256:
        raise SystemExit("session has no frozen base EvaluationContract")
    current_path = run_dir / session.evaluation_contract_ref
    current = EvaluationContract.model_validate_json(current_path.read_text(encoding="utf-8"))
    if current.revision == 0:
        return session.evaluation_contract_ref, session.evaluation_contract_sha256, current
    candidates = [
        path for path in current_path.parent.glob("evaluation_contract_*.json")
        if EvaluationContract.model_validate_json(path.read_text(encoding="utf-8")).revision == 0
    ]
    if len(candidates) != 1:
        raise SystemExit("07H calibration requires exactly one preserved revision-0 EvaluationContract")
    path = candidates[0]
    return str(path.relative_to(run_dir)), sha256_file(path), EvaluationContract.model_validate_json(path.read_text(encoding="utf-8"))


def _validate_attempt(run_dir: Path, attempt_id: str, *, base_ref: str, base_sha: str) -> dict:
    attempt = _load(run_dir / "experiments" / "attempts" / f"{attempt_id}.json")
    if attempt.get("runtime_status") != "COMPLETED":
        raise ValueError(f"{attempt_id} is not completed")
    if attempt.get("evaluation_contract_ref") != base_ref or attempt.get("evaluation_contract_sha256") != base_sha:
        raise ValueError(f"{attempt_id} is not bound to the base EvaluationContract")
    command = attempt.get("command_plan")
    if not isinstance(command, dict):
        raise ValueError(f"{attempt_id} has no command plan")
    seed = _seed_from_command(command, attempt_id)
    card = _load(run_dir / "attempts" / attempt_id / "outcome_card.json")
    if card.get("protocol_intact") is not True:
        raise ValueError(f"{attempt_id} does not have intact protocol evidence")
    before = _load(run_dir / "attempts" / attempt_id / "protected_hash_before.json")
    after = _load(run_dir / "attempts" / attempt_id / "protected_hash_after.json")
    if before != after:
        raise ValueError(f"{attempt_id} changed protected hashes")
    metrics = _load(run_dir / "attempts" / attempt_id / "metrics.json")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(f"{attempt_id} has no parsed metrics")
    refs = attempt.get("input_refs")
    if not isinstance(refs, dict):
        raise ValueError(f"{attempt_id} has invalid input refs")
    command_file = _command_file(command, attempt_id)
    return {
        "attempt_id": attempt_id,
        "seed": seed,
        "input_refs": refs,
        "command_shape": _normalized_command_shape(command, command_file),
        "protected_hashes": before,
        "metrics": metrics,
    }


def _seed_from_command(command: dict, attempt_id: str) -> int:
    command_id = command.get("command_id")
    environment = command.get("environment")
    if not isinstance(command_id, str) or not isinstance(environment, dict):
        raise ValueError(f"{attempt_id} command evidence is invalid")
    for candidate in [0, 1, 2]:
        if f"baseline_seed_{candidate}_" == command_id[: len(f"baseline_seed_{candidate}_")]:
            if environment.get("PYTHONHASHSEED") != str(candidate):
                raise ValueError(f"{attempt_id} command ID and PYTHONHASHSEED disagree")
            return candidate
    raise ValueError(f"{attempt_id} command ID has no allowed seed component")


def _validate_invariants(evidence: list[dict]) -> None:
    first = {key: value for key, value in evidence[0]["input_refs"].items() if key != "command_sha256"}
    first_command = evidence[0]["command_shape"]
    first_protected = evidence[0]["protected_hashes"]
    for item in evidence[1:]:
        refs = {key: value for key, value in item["input_refs"].items() if key != "command_sha256"}
        if refs != first:
            raise ValueError("included Attempts have different repository, environment, dataset, or asset inputs")
        if item["command_shape"] != first_command:
            raise ValueError("included Attempts differ in more than their approved seed command fields")
        if item["protected_hashes"] != first_protected:
            raise ValueError("included Attempts have different protected hash evidence")


def _retrospective_noise_floors(session_id: str, category: str, evidence: list[dict], *, updated_at: str) -> list[dict]:
    metric_names = sorted(set().union(*(item["metrics"].keys() for item in evidence)))
    if any(set(item["metrics"]) != set(metric_names) for item in evidence):
        raise ValueError("included Attempts do not expose the same metrics")
    return [
        calibrate_noise_floor(
            session_id=session_id,
            metric=metric,
            category=category,
            samples=[float(item["metrics"][metric]) for item in evidence],
            retrospective=True,
        ).model_copy(update={"updated_at": updated_at}).model_dump(mode="json")
        for metric in metric_names
    ]


def _command_file(command: dict, attempt_id: str) -> dict:
    args = command.get("args")
    if not isinstance(args, list):
        raise ValueError(f"{attempt_id} command args are invalid")
    try:
        path = Path(str(args[args.index("--command-file") + 1]))
    except (ValueError, IndexError) as exc:
        raise ValueError(f"{attempt_id} command has no command file") from exc
    return _load(path)


def _normalized_command_shape(command: dict, command_file: dict) -> dict:
    outer = json.loads(json.dumps(command))
    outer.pop("command_id", None)
    environment = outer.get("environment")
    if isinstance(environment, dict):
        environment.pop("PYTHONHASHSEED", None)
        environment.pop("AUTOAD_PATCHCORE_COMMAND_SHA256", None)
    args = outer.get("args")
    if isinstance(args, list):
        for flag, placeholder in (("--command-file", "<seeded-command-file>"), ("--command-sha256", "<seeded-command-sha256>")):
            if flag in args:
                args[args.index(flag) + 1] = placeholder
    inner = json.loads(json.dumps(command_file))
    inner.pop("command_id", None)
    inner.pop("command_sha256", None)
    argv = inner.get("argv")
    if isinstance(argv, list) and "--seed" in argv:
        index = argv.index("--seed")
        del argv[index:index + 2]
    return {"outer": outer, "inner": inner}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict) -> None:
    if path.is_file():
        if _load(path) != payload:
            raise ValueError(f"immutable artifact already exists with different content: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
