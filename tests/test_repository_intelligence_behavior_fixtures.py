"""Repository Intelligence R14 acquisition profile and behavior fixtures."""

import hashlib
import subprocess
from pathlib import Path

import httpx

from autoad_researcher.repository_intelligence import (
    RepositoryAcquisitionRequest,
    RepositoryAcquisitionRunner,
    RepositoryDiscoveryService,
    RepositoryIntelligenceRequest,
    build_discovery_queries,
)
from autoad_researcher.tools import GitHubReadProvider, WebFetchResult, WebSearchResult
from autoad_researcher.tools.providers import RecordedWebSearchProvider


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
COMMIT_C = "c" * 40


def request(**overrides) -> RepositoryIntelligenceRequest:
    data = {
        "schema_version": 1,
        "request_id": "req_fixture",
        "run_id": "run_fixture",
        "user_goal": "identify repository implementation",
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
    metadata = {
        ("example", "PatchCore"): {"sha": COMMIT_A, "default_branch": "main", "fork": False, "archived": False},
        ("official", "PatchCore"): {"sha": COMMIT_B, "default_branch": "main", "fork": False, "archived": True},
        ("forklab", "PatchCore"): {"sha": COMMIT_C, "default_branch": "main", "fork": True, "archived": False},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        parts = [part for part in req.url.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "repos":
            owner, repo = parts[1], parts[2]
            record = metadata.get((owner, repo))
            if record is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(
                200,
                json={
                    "owner": {"login": owner},
                    "name": repo,
                    "default_branch": record["default_branch"],
                    "fork": record["fork"],
                    "archived": record["archived"],
                    "html_url": f"https://github.com/{owner}/{repo}",
                },
            )
        if len(parts) == 5 and parts[0] == "repos" and parts[3] == "commits":
            owner, repo, ref = parts[1], parts[2], parts[4]
            record = metadata.get((owner, repo))
            if record is None or ref != record["default_branch"]:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json={"sha": record["sha"]})
        return httpx.Response(404, json={"message": "not found"})

    return GitHubReadProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), api_base="https://api.github.test")


class FailingSearchProvider:
    def search(self, query: str) -> list[WebSearchResult]:
        raise AssertionError(f"unexpected WebSearch call: {query}")


class RecordedFetchProvider:
    def __init__(self, records: dict[str, str]):
        self.records = records
        self.calls: list[str] = []

    def fetch(self, url: str) -> WebFetchResult:
        self.calls.append(url)
        content = self.records[url]
        return WebFetchResult(
            url=url,
            status_code=200,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )


class FailingFetchProvider:
    def fetch(self, url: str) -> WebFetchResult:
        raise AssertionError(f"unexpected WebFetch call: {url}")


def service(
    records: dict[str, list[WebSearchResult]] | None = None,
    *,
    web_fetch: RecordedFetchProvider | FailingFetchProvider | None = None,
) -> RepositoryDiscoveryService:
    return RepositoryDiscoveryService(
        web_search=RecordedWebSearchProvider(records or {}),
        github_read=github_provider(),
        web_fetch=web_fetch,
    )


def run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=True)


def make_remote_repo(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    run(["git", "init", "-b", "main"], cwd=source)
    (source / "README.md").write_text("fixture repository\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=source)
    run(["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "initial"], cwd=source)
    commit = run(["git", "rev-parse", "HEAD"], cwd=source).stdout.strip()
    return source, commit


def test_fixture_explicit_url_skips_search_and_fetch():
    result = RepositoryDiscoveryService(
        web_search=FailingSearchProvider(),
        github_read=github_provider(),
        web_fetch=FailingFetchProvider(),
    ).discover(request(repository_url="https://github.com/example/PatchCore"))

    assert result.stage_status == "resolved"
    assert result.search_queries.queries == []
    assert result.web_search_results == []
    assert result.fetched_pages == []
    assert result.repository_resolution is not None
    assert result.repository_resolution.resolved_commit == COMMIT_A


def test_fixture_project_page_with_single_github_link_resolves():
    req = request(paper_title="PatchCore Paper")
    query = build_discovery_queries(req)[0]
    fetch = RecordedFetchProvider(
        {
            "https://example.org/patchcore": '<a href="https://github.com/example/PatchCore">code</a>',
        }
    )

    result = service(
        {
            query: [
                WebSearchResult(
                    title="PatchCore project page",
                    url="https://example.org/patchcore",
                    snippet="official project page with code",
                )
            ]
        },
        web_fetch=fetch,
    ).discover(req)

    assert result.stage_status == "resolved"
    assert fetch.calls == ["https://example.org/patchcore"]
    assert len(result.fetched_pages) == 1
    assert result.repository_candidates[0].canonical_url == "https://github.com/example/PatchCore"
    assert result.repository_candidates[0].evidence_ids == ["ev_web_page_001", "ev_github_metadata_001"]
    assert result.repository_resolution is not None
    assert result.repository_resolution.resolved_commit == COMMIT_A


def test_fixture_archived_official_and_active_fork_require_user_selection():
    req = request()
    query = build_discovery_queries(req)[0]

    result = service(
        {
            query: [
                WebSearchResult(title="official archived code", url="https://github.com/official/PatchCore", snippet="official"),
                WebSearchResult(title="active reproduction fork", url="https://github.com/forklab/PatchCore", snippet="fork"),
            ]
        }
    ).discover(req)

    assert result.stage_status == "needs_user_confirmation"
    assert result.repository_resolution is not None
    assert result.repository_resolution.user_confirmation_required is True
    assert result.repository_resolution.alternative_candidate_ids == ["cand_001", "cand_002"]
    assert result.repository_candidates[0].warnings == ["candidate repository is archived"]
    assert result.repository_candidates[1].warnings == ["candidate repository is a fork"]


def test_fixture_no_candidate_is_not_found():
    result = service().discover(request())

    assert result.stage_status == "not_found"
    assert result.repository_resolution is not None
    assert result.repository_resolution.status == "not_found"
    assert result.repository_candidates == []


def test_fixture_shallow_ref_uses_depth_without_partial_filter(tmp_path: Path):
    remote, commit = make_remote_repo(tmp_path)
    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_shallow",
            workspace_root=tmp_path / "workspace",
            remote_url=remote.as_posix(),
            resolved_ref="main",
            resolved_commit=commit,
            acquisition_profile="shallow_ref",
        ),
        run_dir=tmp_path / "run",
    )

    assert result.status == "success"
    fetch_call = next(call for call in result.tool_calls if call.tool_call_id == "tool_git_fetch")
    assert "--depth=1" in fetch_call.argv
    assert "--filter=blob:none" not in fetch_call.argv
    assert (tmp_path / "run" / "repository_source.json").is_file()


def test_fixture_partial_exact_missing_commit_is_structured_failure(tmp_path: Path):
    remote, _commit = make_remote_repo(tmp_path)
    result = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_partial",
            workspace_root=tmp_path / "workspace",
            remote_url=remote.as_posix(),
            resolved_commit="f" * 40,
            acquisition_profile="partial_exact",
        ),
        run_dir=tmp_path / "run",
    )

    assert result.status == "failed"
    assert result.source is None
    assert result.error_code == "ACQUISITION_FAILED"
    assert "SOURCE_COMMIT_MISMATCH" in (result.error_message or "")
    assert not (tmp_path / "run" / "repository_source.json").exists()
