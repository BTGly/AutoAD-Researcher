from autoad_researcher.experiment.noise_floor import NoiseFloorStore, calibrate_noise_floor
from autoad_researcher.experiment.noise_calibration import (
    NoiseCalibrationProtocol,
    NoiseCalibrationProtocolStore,
)


def test_noise_floor_progresses_from_uncalibrated_to_locked_and_context_persistence(tmp_path):
    provisional = calibrate_noise_floor(session_id="session", metric="auroc", category="bottle", samples=[.7, .71, .69])
    assert provisional.status == "PROVISIONAL_NOISE_FLOOR"
    locked = calibrate_noise_floor(session_id="session", metric="auroc", category="bottle", samples=[.7, .71, .69, .70, .72])
    assert locked.status == "LOCKED"
    NoiseFloorStore().save(tmp_path, locked)
    assert NoiseFloorStore().load_for_session(tmp_path, session_id="session") == [locked]


def test_retrospective_protocol_is_immutable_and_never_locks_noise_floor(tmp_path):
    protocol = NoiseCalibrationProtocol(
        protocol_id="noise_calibration_000001",
        session_id="session",
        base_evaluation_contract_ref="experiments/evaluation_contracts/session/evaluation_contract_000001.json",
        base_evaluation_contract_sha256="a" * 64,
        allowed_seed_set=[0, 1, 2],
        invariant_fields=["repository_fingerprint", "dataset_manifest_sha256"],
        variable_fields=["seed", "PYTHONHASHSEED", "command_id seed component"],
        included_attempts=["attempt_000002", "attempt_000004", "attempt_000005"],
        excluded_attempts=["attempt_000003"],
        retrospective_or_prospective="retrospective",
        created_at="2026-07-18T00:00:00+00:00",
    )
    ref = NoiseCalibrationProtocolStore().freeze(tmp_path, protocol=protocol)
    assert ref.endswith("noise_calibration_000001.json")
    assert NoiseCalibrationProtocolStore().freeze(tmp_path, protocol=protocol) == ref
    floor = calibrate_noise_floor(
        session_id="session",
        metric="auroc",
        category="bottle",
        samples=[.7, .71, .69, .70, .72],
        retrospective=True,
    )
    assert floor.status == "PROVISIONAL_RETROSPECTIVE"
