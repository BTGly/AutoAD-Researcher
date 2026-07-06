"""Tests for Repository Intelligence R4 harness and model routing."""

from pathlib import Path

from pydantic import ValidationError

from autoad_researcher.repository_intelligence import (
    AnalysisControlSignal,
    ModelRouter,
    RepositoryAgentBudget,
    RepositoryIntelligenceHarness,
    RepositoryIntelligenceRequest,
    RepositoryModelConfig,
    default_repository_tool_registry,
    load_model_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "skills"
MODEL_CONFIG = PROJECT_ROOT / "configs" / "models" / "repository_intelligence.yaml"


def budget() -> RepositoryAgentBudget:
    return RepositoryAgentBudget(
        max_total_tool_calls=100,
        max_total_llm_calls=20,
        max_total_input_tokens=100_000,
        max_total_output_tokens=20_000,
        max_discovery_search_calls=5,
        max_discovery_fetch_calls=5,
        max_analysis_tool_calls=60,
        max_analysis_file_reads=40,
        max_analysis_search_calls=20,
        max_analysis_llm_calls=10,
        max_repair_tool_calls=10,
        max_repair_llm_calls=3,
        max_repairs=2,
        max_no_progress_cycles=2,
    )


def request(**overrides) -> RepositoryIntelligenceRequest:
    data = {
        "schema_version": 1,
        "request_id": "req_001",
        "run_id": "run_demo",
        "user_goal": "analyze repository",
        "discovery_allowed": True,
        "user_confirmation_policy": "when_ambiguous",
        "budget_profile": "custom",
        "budget": budget(),
    }
    data.update(overrides)
    return RepositoryIntelligenceRequest(**data)


def test_model_config_loads_fixed_profiles():
    config = load_model_config(MODEL_CONFIG)

    assert set(config.profiles) == {"repository_fast_v1", "repository_primary_v1", "repository_fallback_v1"}
    assert config.profiles["repository_primary_v1"].model_id == "deepseek-v4-pro"
    assert config.profiles["repository_primary_v1"].minimum_context_window == 128000
    assert len(config.config_sha256) == 64


def test_model_config_rejects_latest_alias():
    raw = load_model_config(MODEL_CONFIG).model_dump(mode="json")
    raw["profiles"]["repository_fast_v1"]["model_id"] = "latest"

    try:
        RepositoryModelConfig.model_validate(raw)
    except ValidationError as exc:
        assert "latest aliases" in str(exc)
    else:
        raise AssertionError("latest alias should be rejected")


def test_model_router_routes_fast_and_primary_profiles():
    router = ModelRouter(load_model_config(MODEL_CONFIG))

    fast = router.route(stage="discovery", purpose="simple_discovery")
    primary = router.route(stage="analysis", purpose="analysis")

    assert fast.selected_profile == "repository_fast_v1"
    assert primary.selected_profile == "repository_primary_v1"


def test_fallback_gate_blocks_without_consuming_quota_when_unavailable():
    raw = load_model_config(MODEL_CONFIG).model_dump(mode="json")
    raw["profiles"]["repository_fallback_v1"]["availability"] = "unavailable"
    router = ModelRouter(RepositoryModelConfig.model_validate(raw))

    decision = router.route(
        stage="analysis",
        purpose="analysis",
        primary_failed=True,
        fallback_attempts_used=0,
    )

    assert decision.status == "blocked"
    assert decision.capability_gate_passed is False
    assert decision.fallback_attempts_used == 0
    assert decision.blocked_reason == "fallback provider unavailable"


def test_harness_stage_entry_loads_skill_tools_model_and_resume_fingerprint(tmp_path: Path):
    harness = RepositoryIntelligenceHarness(
        runs_root=tmp_path,
        skills_root=SKILLS_ROOT,
        model_config_path=MODEL_CONFIG,
    )

    record = harness.enter_stage(
        request=request(),
        stage="analysis",
        route_purpose="analysis",
        loaded_at="2026-06-17T00:00:00Z",
    )

    assert record.skill_loaded is True
    assert record.loaded_skill is not None
    assert record.loaded_skill.skill_name == "repository-analysis"
    assert {r.tool_name for r in record.loaded_tools} == {
        "filesystem_list",
        "filesystem_read",
        "filesystem_search",
        "filesystem_stat",
        "process",
    }
    assert record.model_routing.selected_profile == "repository_primary_v1"
    assert len(record.resume_fingerprint) == 64
    assert (tmp_path / "run_demo" / "stage_entry_analysis.json").is_file()
    assert (tmp_path / "run_demo" / "model_routing_decisions.jsonl").is_file()


def test_harness_skips_discovery_skill_for_explicit_source(tmp_path: Path):
    harness = RepositoryIntelligenceHarness(
        runs_root=tmp_path,
        skills_root=SKILLS_ROOT,
        model_config_path=MODEL_CONFIG,
    )

    record = harness.enter_stage(
        request=request(repository_url="https://github.com/example/repo"),
        stage="discovery",
        route_purpose="simple_discovery",
        loaded_at="2026-06-17T00:00:00Z",
    )

    assert record.skill_loaded is False
    assert record.loaded_skill is None
    assert record.model_routing.selected_profile == "repository_fast_v1"


def test_synthesis_ready_requires_minimum_coverage(tmp_path: Path):
    harness = RepositoryIntelligenceHarness(
        runs_root=tmp_path,
        skills_root=SKILLS_ROOT,
        model_config_path=MODEL_CONFIG,
    )

    decision = harness.record_analysis_control_signal(
        request=request(),
        signal=AnalysisControlSignal(
            decision="synthesis_ready",
            coverage={"repository_summary": "confirmed"},
            new_evidence_count=1,
        ),
        no_progress_cycles=0,
        remaining_analysis_tool_calls=10,
    )

    assert decision.decision == "continue_reading"
    assert "missing coverage" in decision.reason
    assert (tmp_path / "run_demo" / "analysis_control_signals.jsonl").is_file()


def test_complete_coverage_allows_synthesis_ready(tmp_path: Path):
    harness = RepositoryIntelligenceHarness(
        runs_root=tmp_path,
        skills_root=SKILLS_ROOT,
        model_config_path=MODEL_CONFIG,
    )
    coverage = {
        "repository_summary": "confirmed",
        "entrypoints": "confirmed",
        "dependencies": "checked_unknown",
        "configurations": "confirmed",
        "evaluation": "conflicting",
        "data_assets": "checked_unknown",
    }

    decision = harness.record_analysis_control_signal(
        request=request(),
        signal=AnalysisControlSignal(
            decision="synthesis_ready",
            coverage=coverage,
            new_evidence_count=3,
        ),
        no_progress_cycles=0,
        remaining_analysis_tool_calls=10,
    )

    assert decision.decision == "synthesis_ready"


def test_no_progress_forces_synthesis(tmp_path: Path):
    harness = RepositoryIntelligenceHarness(
        runs_root=tmp_path,
        skills_root=SKILLS_ROOT,
        model_config_path=MODEL_CONFIG,
    )

    decision = harness.record_analysis_control_signal(
        request=request(),
        signal=AnalysisControlSignal(
            decision="continue_reading",
            coverage={},
            new_evidence_count=0,
        ),
        no_progress_cycles=2,
        remaining_analysis_tool_calls=10,
    )

    assert decision.decision == "forced_synthesis"


def test_default_registry_contains_required_repository_tools():
    registry = default_repository_tool_registry()

    assert sorted(registry.tools) == [
        "filesystem_list",
        "filesystem_read",
        "filesystem_search",
        "filesystem_stat",
        "git_clone",
        "github_read",
        "process",
        "web_fetch",
        "web_search",
    ]
