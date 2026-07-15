"""Tests for Repository Intelligence R5 discovery and resolution."""

import base64
from pathlib import Path

import httpx
import pytest

from autoad_researcher.repository_intelligence import (
    DiscoveryError,
    RepositoryDiscoveryService,
    RepositoryIntelligenceRequest,
    build_discovery_queries,
    parse_github_repository_url,
    write_discovery_artifacts,
)
from autoad_researcher.tools import GitHubReadProvider, RecordedWebSearchProvider, WebSearchResult, load_stage_tool_specs
from autoad_researcher.repository_intelligence.harness import default_repository_tool_registry


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def request(**overrides) -> RepositoryIntelligenceRequest:
    data = {
        "schema_version": 1,
        "request_id": "req_001",
        "run_id": "run_demo",
        "user_goal": "find repository",
        "project_name": "PatchCore",
        "method_name": "PatchCore",
        "authors": ["Example Lab"],
        "keywords": ["anomaly"],
        "discovery_allowed": True,
        "user_confirmation_policy": "when_ambiguous",
        "budget_profile": "small",
    }
    data.update(overrides)
    return RepositoryIntelligenceRequest(**data)


def github_provider() -> GitHubReadProvider:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/repos/example/PatchCore":
            return httpx.Response(
                200,
                json={
                    "owner": {"login": "example"},
                    "name": "PatchCore",
                    "default_branch": "main",
                    "fork": False,
                    "archived": False,
                    "html_url": "https://github.com/example/PatchCore",
                },
            )
        if path == "/repos/other/PatchCore":
            return httpx.Response(
                200,
                json={
                    "owner": {"login": "other"},
                    "name": "PatchCore",
                    "default_branch": "master",
                    "fork": False,
                    "archived": False,
                    "html_url": "https://github.com/other/PatchCore",
                },
            )
        if path == "/repos/example/PatchCore/commits/main":
            return httpx.Response(200, json={"sha": COMMIT_A})
        if path == "/repos/other/PatchCore/commits/master":
            return httpx.Response(200, json={"sha": COMMIT_B})
        if path == "/repos/example/PatchCore/contents/README.md":
            return httpx.Response(
                200,
                json={"encoding": "base64", "content": base64.b64encode(b"readme").decode(), "sha": "blob_sha"},
            )
        return httpx.Response(404, json={"message": "not found"})

    return GitHubReadProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), api_base="https://api.github.test")


def service(records: dict[str, list[WebSearchResult]] | None = None) -> RepositoryDiscoveryService:
    return RepositoryDiscoveryService(
        web_search=RecordedWebSearchProvider(records or {}),
        github_read=github_provider(),
    )


def test_parse_github_repository_url_rejects_credentials():
    with pytest.raises(DiscoveryError, match="credential-bearing"):
        parse_github_repository_url("https://token@github.com/example/PatchCore")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/PatchCore/issues",
        "https://github.com/example/PatchCore?tab=readme",
        "https://github.com/example/PatchCore#readme",
        "https://github.com/example/PatchCore，分析这个仓库",
    ],
)
def test_parse_github_repository_url_requires_exact_owner_repository_root(url: str):
    with pytest.raises(DiscoveryError, match="must match"):
        parse_github_repository_url(url)
    assert parse_github_repository_url(url, strict=False) is None


def test_explicit_github_url_skips_websearch_and_resolves_default_branch_commit():
    result = service().discover(request(repository_url="https://github.com/example/PatchCore"))

    assert result.stage_status == "resolved"
    assert result.skip_reason == "explicit repository URL bypasses WebSearch discovery"
    assert result.search_queries.queries == []
    assert result.repository_resolution is not None
    assert result.repository_resolution.resolved_ref == "main"
    assert result.repository_resolution.resolved_commit == COMMIT_A
    assert result.repository_candidates[0].default_branch == "main"


def test_local_source_skips_discovery_without_resolution():
    result = service().discover(request(local_path="workspace/repos/local_patchcore"))

    assert result.stage_status == "skipped"
    assert result.skip_reason == "local source bypasses repository discovery"
    assert result.repository_resolution is None
    assert result.repository_candidates == []


def test_build_discovery_queries_is_small_and_deterministic():
    queries = build_discovery_queries(request(paper_title="Towards Total Recall in Industrial Anomaly Detection"))

    assert queries == [
        "PatchCore PatchCore Towards Total Recall in Industrial Anomaly Detection Example Lab GitHub",
        '"Towards Total Recall in Industrial Anomaly Detection" code GitHub',
        '"PatchCore" repository',
    ]


def test_single_strong_candidate_resolves_and_search_evidence_is_association_only():
    query = build_discovery_queries(request())[0]
    result = service(
        {
            query: [
                WebSearchResult(
                    title="PatchCore",
                    url="https://github.com/example/PatchCore",
                    snippet="official code",
                )
            ]
        }
    ).discover(request())

    assert result.stage_status == "resolved"
    assert result.repository_resolution is not None
    assert result.repository_resolution.selected_candidate_id == "cand_001"
    assert result.web_search_results[0].evidence.source_kind == "search_result"
    assert result.web_search_results[0].evidence.trust_level == "association_lead"
    assert result.repository_candidates[0].evidence_ids == ["ev_search_001_001", "ev_github_metadata_001"]


def test_multiple_candidates_require_user_confirmation():
    query = build_discovery_queries(request())[0]
    result = service(
        {
            query: [
                WebSearchResult(title="PatchCore A", url="https://github.com/example/PatchCore", snippet="code"),
                WebSearchResult(title="PatchCore B", url="https://github.com/other/PatchCore", snippet="code"),
            ]
        }
    ).discover(request())

    assert result.stage_status == "needs_user_confirmation"
    assert result.repository_resolution is not None
    assert result.repository_resolution.user_confirmation_required is True
    assert result.repository_resolution.alternative_candidate_ids == ["cand_001", "cand_002"]


def test_discovery_disabled_without_source_blocks():
    result = service().discover(request(discovery_allowed=False, project_name=None, method_name=None))

    assert result.stage_status == "blocked"
    assert result.repository_resolution is not None
    assert result.repository_resolution.status == "blocked"


def test_discovery_artifacts_are_written_without_overwrite(tmp_path: Path):
    result = service().discover(request(repository_url="https://github.com/example/PatchCore"))

    write_discovery_artifacts(tmp_path, result)

    assert (tmp_path / "input_signals.json").is_file()
    assert (tmp_path / "search_queries.json").is_file()
    assert (tmp_path / "web_search_results.json").is_file()
    assert (tmp_path / "fetched_pages.json").is_file()
    assert (tmp_path / "repository_candidates.json").is_file()
    assert (tmp_path / "repository_resolution.json").is_file()

    with pytest.raises(FileExistsError):
        write_discovery_artifacts(tmp_path, result)


def test_discovery_stage_loads_only_allowed_web_and_github_tools():
    loaded = load_stage_tool_specs(
        registry=default_repository_tool_registry(),
        stage="discovery",
        trigger_reason="stage_entry",
        loaded_at="2026-06-17T00:00:00Z",
    )

    assert [spec.name for spec in loaded.specs] == ["github_read", "web_fetch", "web_search"]
