"""Repository discovery and resolution helpers for Step 3.1 R5."""

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.repository_intelligence.evidence_models import WebEvidenceRef
from autoad_researcher.repository_intelligence.models import (
    RepositoryCandidate,
    RepositoryIntelligenceRequest,
    RepositoryResolution,
)
from autoad_researcher.tools.providers import (
    GitHubReadProvider,
    RecordedWebSearchProvider,
    SecureWebFetchProvider,
    WebFetchResult,
    WebSearchResult,
)


class DiscoveryError(ValueError):
    """Raised when repository discovery cannot safely continue."""


class GitHubRepositoryLocator(BaseModel):
    """Parsed public GitHub repository locator."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)


class RepositoryInputSignals(BaseModel):
    """Auditable input signals used to build discovery queries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    repository_url: str | None = None
    local_path: str | None = None
    requested_ref: str | None = None
    paper_title: str | None = None
    paper_url: str | None = None
    project_name: str | None = None
    method_name: str | None = None
    authors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class RepositorySearchQueries(BaseModel):
    """Deterministic discovery queries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    queries: list[str]


class RepositorySearchResultRecord(BaseModel):
    """One WebSearch result with association-only evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    query: str
    rank: int = Field(ge=1)
    result: WebSearchResult
    evidence: WebEvidenceRef


class RepositoryFetchedPageRecord(BaseModel):
    """One fetched page with association-only evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    fetch: WebFetchResult
    evidence: WebEvidenceRef


class RepositoryDiscoveryResult(BaseModel):
    """Discovery and resolution artifacts produced by R5."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    stage_status: Literal["skipped", "resolved", "needs_user_confirmation", "not_found", "blocked"]
    skip_reason: str | None = None
    input_signals: RepositoryInputSignals
    search_queries: RepositorySearchQueries
    web_search_results: list[RepositorySearchResultRecord] = Field(default_factory=list)
    fetched_pages: list[RepositoryFetchedPageRecord] = Field(default_factory=list)
    repository_candidates: list[RepositoryCandidate] = Field(default_factory=list)
    repository_resolution: RepositoryResolution | None = None


class RepositoryDiscoveryService:
    """Deterministic R5 discovery/resolution service.

    Providers are injected so CI can use recorded or mocked responses. This
    service does not run repository code or read acquired source files.
    """

    def __init__(
        self,
        *,
        web_search: RecordedWebSearchProvider | None,
        github_read: GitHubReadProvider,
        web_fetch: SecureWebFetchProvider | None = None,
    ):
        self.web_search = web_search
        self.github_read = github_read
        self.web_fetch = web_fetch

    def discover(self, request: RepositoryIntelligenceRequest) -> RepositoryDiscoveryResult:
        """Discover and resolve a repository candidate for `request`."""
        input_signals = RepositoryInputSignals(
            schema_version=1,
            repository_url=request.repository_url,
            local_path=request.local_path,
            requested_ref=request.requested_ref,
            paper_title=request.paper_title,
            paper_url=request.paper_url,
            project_name=request.project_name,
            method_name=request.method_name,
            authors=request.authors,
            keywords=request.keywords,
        )

        if request.local_path is not None:
            return RepositoryDiscoveryResult(
                schema_version=1,
                stage_status="skipped",
                skip_reason="local source bypasses repository discovery",
                input_signals=input_signals,
                search_queries=RepositorySearchQueries(schema_version=1, queries=[]),
            )

        if request.repository_url is not None:
            candidate = self._candidate_from_github_url(
                request.repository_url,
                requested_ref=request.requested_ref,
                candidate_id="cand_001",
                evidence_ids=["ev_github_metadata_001"],
                selection_rationale="explicit GitHub repository URL supplied by user",
            )
            resolution = _resolved(candidate, reason="explicit GitHub repository URL supplied by user")
            return RepositoryDiscoveryResult(
                schema_version=1,
                stage_status="resolved",
                skip_reason="explicit repository URL bypasses WebSearch discovery",
                input_signals=input_signals,
                search_queries=RepositorySearchQueries(schema_version=1, queries=[]),
                repository_candidates=[candidate],
                repository_resolution=resolution,
            )

        if not request.discovery_allowed:
            resolution = RepositoryResolution(
                schema_version=1,
                status="blocked",
                selected_candidate_id=None,
                alternative_candidate_ids=[],
                resolved_ref=None,
                resolved_commit=None,
                resolution_reason="discovery is disabled and no explicit source was supplied",
                user_confirmation_required=False,
            )
            return RepositoryDiscoveryResult(
                schema_version=1,
                stage_status="blocked",
                input_signals=input_signals,
                search_queries=RepositorySearchQueries(schema_version=1, queries=[]),
                repository_resolution=resolution,
            )

        if self.web_search is None:
            raise DiscoveryError("web_search provider is required when discovery_allowed is true")

        queries = build_discovery_queries(request)
        search_records: list[RepositorySearchResultRecord] = []
        fetched_pages: list[RepositoryFetchedPageRecord] = []
        candidates: list[RepositoryCandidate] = []
        seen_repos: set[tuple[str, str]] = set()

        for query_index, query in enumerate(queries, start=1):
            for rank, result in enumerate(self.web_search.search(query), start=1):
                evidence = WebEvidenceRef(
                    source_kind="search_result",
                    evidence_id=f"ev_search_{query_index:03d}_{rank:03d}",
                    url=result.url,
                    content_sha256=_search_result_sha(result),
                    tool_call_id=f"tool_web_search_{query_index:03d}",
                    trust_level="association_lead",
                )
                search_records.append(
                    RepositorySearchResultRecord(
                        schema_version=1,
                        query=query,
                        rank=rank,
                        result=result,
                        evidence=evidence,
                    )
                )

                locator = parse_github_repository_url(result.url, strict=False)
                if locator is not None and (locator.owner, locator.repository) not in seen_repos:
                    seen_repos.add((locator.owner, locator.repository))
                    candidates.append(
                        self._candidate_from_locator(
                            locator,
                            requested_ref=request.requested_ref,
                            candidate_id=f"cand_{len(candidates) + 1:03d}",
                            evidence_ids=[evidence.evidence_id, f"ev_github_metadata_{len(candidates) + 1:03d}"],
                            selection_rationale=f"GitHub repository found in WebSearch result for query: {query}",
                            request=request,
                        )
                    )
                elif self.web_fetch is not None and _looks_fetchable_project_page(result.url):
                    fetched_pages.append(self._fetch_page(result.url, len(fetched_pages) + 1))

        resolution = resolve_candidates(candidates, request=request)
        return RepositoryDiscoveryResult(
            schema_version=1,
            stage_status=resolution.status,
            input_signals=input_signals,
            search_queries=RepositorySearchQueries(schema_version=1, queries=queries),
            web_search_results=search_records,
            fetched_pages=fetched_pages,
            repository_candidates=candidates,
            repository_resolution=resolution,
        )

    def _candidate_from_github_url(
        self,
        url: str,
        *,
        requested_ref: str | None,
        candidate_id: str,
        evidence_ids: list[str],
        selection_rationale: str,
    ) -> RepositoryCandidate:
        locator = parse_github_repository_url(url, strict=True)
        if locator is None:
            raise DiscoveryError(f"not a supported GitHub repository URL: {url}")
        return self._candidate_from_locator(
            locator,
            requested_ref=requested_ref,
            candidate_id=candidate_id,
            evidence_ids=evidence_ids,
            selection_rationale=selection_rationale,
            request=None,
        )

    def _candidate_from_locator(
        self,
        locator: GitHubRepositoryLocator,
        *,
        requested_ref: str | None,
        candidate_id: str,
        evidence_ids: list[str],
        selection_rationale: str,
        request: RepositoryIntelligenceRequest | None,
    ) -> RepositoryCandidate:
        metadata = self.github_read.repository_metadata(locator.owner, locator.repository)
        resolved_ref = requested_ref or metadata.default_branch
        commit = self.github_read.commit_ref(metadata.owner, metadata.repository, resolved_ref)
        method_match = _method_match(request, metadata.html_url, metadata.repository) if request is not None else "strong"
        official = request.repository_url is not None if request is not None else True
        return RepositoryCandidate(
            candidate_id=candidate_id,
            canonical_url=metadata.html_url,
            owner=metadata.owner,
            repository=metadata.repository,
            default_branch=metadata.default_branch,
            requested_ref=requested_ref,
            resolved_commit=commit.sha,
            official_link_found=official,
            author_or_org_match=_author_or_org_match(request, metadata.owner) if request is not None else True,
            paper_reference_found=False,
            method_name_match=method_match,
            is_fork=metadata.is_fork,
            is_archived=metadata.is_archived,
            confidence=_candidate_confidence(official=official, method_match=method_match, is_fork=metadata.is_fork, is_archived=metadata.is_archived),
            selection_rationale=selection_rationale,
            evidence_ids=evidence_ids,
            warnings=_candidate_warnings(metadata.is_fork, metadata.is_archived),
        )

    def _fetch_page(self, url: str, index: int) -> RepositoryFetchedPageRecord:
        if self.web_fetch is None:
            raise DiscoveryError("web_fetch provider is not configured")
        result = self.web_fetch.fetch(url)
        return RepositoryFetchedPageRecord(
            schema_version=1,
            fetch=result,
            evidence=WebEvidenceRef(
                source_kind="web_page",
                evidence_id=f"ev_web_page_{index:03d}",
                url=result.url,
                content_sha256=result.content_sha256,
                tool_call_id=f"tool_web_fetch_{index:03d}",
                trust_level="association_lead",
            ),
        )


def build_discovery_queries(request: RepositoryIntelligenceRequest) -> list[str]:
    """Build a small deterministic query set from request signals."""
    signals = [
        request.project_name,
        request.method_name,
        request.paper_title,
        " ".join(request.authors[:2]) if request.authors else None,
    ]
    base = " ".join(signal for signal in signals if signal)
    if not base:
        return []
    queries = [f"{base} GitHub"]
    if request.paper_title:
        queries.append(f'"{request.paper_title}" code GitHub')
    if request.project_name:
        queries.append(f'"{request.project_name}" repository')
    deduped: list[str] = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    return deduped[:3]


def parse_github_repository_url(url: str, *, strict: bool = True) -> GitHubRepositoryLocator | None:
    """Parse supported public GitHub repository URLs."""
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise DiscoveryError("credential-bearing GitHub URLs are forbidden")
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "github.com":
        if strict:
            raise DiscoveryError("only http(s) github.com repository URLs are supported")
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        if strict:
            raise DiscoveryError("GitHub repository URL must include owner and repository")
        return None
    owner = parts[0]
    repository = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    if strict and len(parts) > 2:
        raise DiscoveryError("GitHub repository URL must point to the repository root")
    if not owner or not repository:
        raise DiscoveryError("GitHub repository owner and repository are required")
    return GitHubRepositoryLocator(
        owner=owner,
        repository=repository,
        canonical_url=f"https://github.com/{owner}/{repository}",
    )


def resolve_candidates(
    candidates: list[RepositoryCandidate],
    *,
    request: RepositoryIntelligenceRequest,
) -> RepositoryResolution:
    """Resolve candidates without silently choosing ambiguous results."""
    if not candidates:
        return RepositoryResolution(
            schema_version=1,
            status="not_found",
            selected_candidate_id=None,
            alternative_candidate_ids=[],
            resolved_ref=None,
            resolved_commit=None,
            resolution_reason="no GitHub repository candidates found",
            user_confirmation_required=False,
        )

    viable = [candidate for candidate in candidates if not candidate.is_fork and not candidate.is_archived]
    high_confidence = [candidate for candidate in viable if candidate.confidence == "high"]
    if len(candidates) == 1 and len(high_confidence) == 1 and request.user_confirmation_policy != "always":
        return _resolved(high_confidence[0], reason="single high-confidence candidate")

    if len(high_confidence) == 1 and len(candidates) == 1 and request.user_confirmation_policy != "always":
        return _resolved(high_confidence[0], reason="single high-confidence candidate")

    return RepositoryResolution(
        schema_version=1,
        status="needs_user_confirmation",
        selected_candidate_id=None,
        alternative_candidate_ids=[candidate.candidate_id for candidate in candidates],
        resolved_ref=None,
        resolved_commit=None,
        resolution_reason="multiple or insufficiently strong repository candidates require user confirmation",
        user_confirmation_required=True,
    )


def write_discovery_artifacts(run_dir: Path, result: RepositoryDiscoveryResult) -> None:
    """Write R5 discovery artifacts without overwriting existing files."""
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(run_dir / "input_signals.json", result.input_signals)
    _write_json_atomic(run_dir / "search_queries.json", result.search_queries)
    _write_json_atomic(run_dir / "web_search_results.json", [r.model_dump(mode="json") for r in result.web_search_results])
    _write_json_atomic(run_dir / "fetched_pages.json", [r.model_dump(mode="json") for r in result.fetched_pages])
    _write_json_atomic(run_dir / "repository_candidates.json", [c.model_dump(mode="json") for c in result.repository_candidates])
    if result.repository_resolution is not None:
        _write_json_atomic(run_dir / "repository_resolution.json", result.repository_resolution)


def _resolved(candidate: RepositoryCandidate, *, reason: str) -> RepositoryResolution:
    resolved_ref = candidate.requested_ref or candidate.default_branch
    return RepositoryResolution(
        schema_version=1,
        status="resolved",
        selected_candidate_id=candidate.candidate_id,
        alternative_candidate_ids=[],
        resolved_ref=resolved_ref,
        resolved_commit=candidate.resolved_commit,
        resolution_reason=reason,
        user_confirmation_required=False,
    )


def _search_result_sha(result: WebSearchResult) -> str:
    return canonical_sha256(result)


def _candidate_confidence(*, official: bool, method_match: str, is_fork: bool, is_archived: bool) -> Literal["low", "medium", "high"]:
    if is_fork or is_archived:
        return "low"
    if official or method_match == "strong":
        return "high"
    if method_match == "weak":
        return "medium"
    return "low"


def _candidate_warnings(is_fork: bool, is_archived: bool) -> list[str]:
    warnings: list[str] = []
    if is_fork:
        warnings.append("candidate repository is a fork")
    if is_archived:
        warnings.append("candidate repository is archived")
    return warnings


def _method_match(request: RepositoryIntelligenceRequest | None, url: str, repository: str) -> Literal["none", "weak", "strong"]:
    if request is None:
        return "none"
    haystack = f"{url} {repository}".lower()
    if request.project_name and request.project_name.lower() in haystack:
        return "strong"
    if request.method_name and request.method_name.lower() in haystack:
        return "strong"
    for keyword in request.keywords:
        if keyword.lower() in haystack:
            return "weak"
    return "none"


def _author_or_org_match(request: RepositoryIntelligenceRequest | None, owner: str) -> bool:
    if request is None:
        return False
    owner_lower = owner.lower()
    return any(owner_lower in author.lower().replace(" ", "") for author in request.authors)


def _looks_fetchable_project_page(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() != "github.com"


def _write_json_atomic(path: Path, value: BaseModel | list[dict]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        if isinstance(value, BaseModel):
            payload = value.model_dump(mode="json", exclude_none=True)
        else:
            payload = value
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
