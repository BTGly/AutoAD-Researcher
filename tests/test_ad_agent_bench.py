from autoad_researcher.experiment.ad_agent_bench import ADAgentBench, DEFAULT_CASES


def test_all_plan_defined_bench_cases_pass_and_replay_identically():
    bench = ADAgentBench()
    first, second = bench.replay()
    assert len(first) == 10
    assert [result.case_id for result in first] == [case.case_id for case in DEFAULT_CASES]
    assert all(result.passed for result in first)
    assert [result.model_dump(exclude={"evidence"}) for result in first] == [
        result.model_dump(exclude={"evidence"}) for result in second
    ]
    assert [result.disposition for result in first] == [
        "candidate",
        "bounded_repair",
        "regression",
        "confirm_seed",
        "bounded_repair",
        "archive_failure",
        "rebuild_from_authority",
        "single_compact_cycle",
        "paradigm_shift",
        "reject_result",
    ]
