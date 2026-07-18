from autoad_researcher.experiment.noise_floor import NoiseFloorStore, calibrate_noise_floor


def test_noise_floor_progresses_from_uncalibrated_to_locked_and_context_persistence(tmp_path):
    provisional = calibrate_noise_floor(session_id="session", metric="auroc", category="bottle", samples=[.7, .71, .69])
    assert provisional.status == "PROVISIONAL_NOISE_FLOOR"
    locked = calibrate_noise_floor(session_id="session", metric="auroc", category="bottle", samples=[.7, .71, .69, .70, .72])
    assert locked.status == "LOCKED"
    NoiseFloorStore().save(tmp_path, locked)
    assert NoiseFloorStore().load_for_session(tmp_path, session_id="session") == [locked]
