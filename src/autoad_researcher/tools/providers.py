"""Deferred web and read-only GitHub providers."""

import base64
import hashlib
import ipaddress
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field


class ProviderError(ValueError):
    """Raised when a provider request is unsafe or invalid."""


class RemoteSourceUnavailable(ProviderError):
    """A best-effort remote material could not be obtained safely."""


class WebFetchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    status_code: int
    content: str
    content_sha256: str
    content_bytes: bytes = b""
    content_type: str = ""


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    snippet: str = Field(min_length=1)


class GitHubRepositoryMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner: str
    repository: str
    default_branch: str
    is_fork: bool
    is_archived: bool
    html_url: str


class GitHubCommitRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    owner: str
    repository: str
    ref: str
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class GitHubFileContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    repository: str
    path: str
    ref: str
    text: str
    sha: str


class SecureWebFetchProvider:
    """HTTP(S)-only fetch provider with basic SSRF guards."""

    _MAX_REDIRECTS = 3
    _MAX_CONTENT_BYTES = 50 * 1024 * 1024

    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(follow_redirects=False, timeout=10.0)

    def fetch(self, url: str) -> WebFetchResult:
        safe_url = _validate_public_http_url(url)
        for _ in range(self._MAX_REDIRECTS + 1):
            try:
                response = self._client.get(safe_url)
            except httpx.RequestError as exc:
                raise RemoteSourceUnavailable(
                    "remote_source_unavailable: 当前无法从该链接取得内容，请直接上传 PDF。"
                ) from exc
            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    raise RemoteSourceUnavailable(
                        "remote_source_unavailable: 远程链接返回了无目标的重定向，请直接上传 PDF。"
                    )
                safe_url = _validate_public_http_url(urljoin(safe_url, location))
                continue
            if response.is_error:
                raise RemoteSourceUnavailable(
                    "remote_source_unavailable: 远程链接当前不可用，请直接上传 PDF。"
                )
            break
        else:
            raise RemoteSourceUnavailable(
                "remote_source_unavailable: 重定向次数超出限制，请直接上传 PDF。"
            )

        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > self._MAX_CONTENT_BYTES:
                raise RemoteSourceUnavailable(
                    "remote_source_unavailable: 远程文件超过下载大小限制，请直接上传 PDF。"
                )
        content = response.content
        if len(content) > self._MAX_CONTENT_BYTES:
            raise RemoteSourceUnavailable(
                "remote_source_unavailable: 远程文件超过下载大小限制，请直接上传 PDF。"
            )
        return WebFetchResult(
            url=safe_url,
            status_code=response.status_code,
            content=response.text,
            content_bytes=content,
            content_type=response.headers.get("content-type", ""),
            content_sha256=hashlib.sha256(content).hexdigest(),
        )


class RecordedWebSearchProvider:
    """Deterministic WebSearch provider backed by recorded results."""

    def __init__(self, records: dict[str, list[WebSearchResult]]):
        self._records = records

    def search(self, query: str) -> list[WebSearchResult]:
        return list(self._records.get(query, []))


class GitHubReadProvider:
    """Read-only GitHub REST provider."""

    def __init__(self, client: httpx.Client | None = None, *, api_base: str = "https://api.github.com"):
        self._client = client or httpx.Client(timeout=10.0)
        self._api_base = api_base.rstrip("/")

    def repository_metadata(self, owner: str, repository: str) -> GitHubRepositoryMetadata:
        data = self._get_json(f"/repos/{owner}/{repository}")
        return GitHubRepositoryMetadata(
            owner=data["owner"]["login"],
            repository=data["name"],
            default_branch=data["default_branch"],
            is_fork=bool(data["fork"]),
            is_archived=bool(data["archived"]),
            html_url=data["html_url"],
        )

    def commit_ref(self, owner: str, repository: str, ref: str) -> GitHubCommitRef:
        data = self._get_json(f"/repos/{owner}/{repository}/commits/{ref}")
        return GitHubCommitRef(owner=owner, repository=repository, ref=ref, sha=data["sha"])

    def file_text(self, owner: str, repository: str, path: str, ref: str) -> GitHubFileContent:
        data = self._get_json(f"/repos/{owner}/{repository}/contents/{path}", params={"ref": ref})
        if data.get("encoding") != "base64":
            raise ProviderError("GitHub file content encoding must be base64")
        raw = base64.b64decode(data["content"], validate=False)
        return GitHubFileContent(
            owner=owner,
            repository=repository,
            path=path,
            ref=ref,
            text=raw.decode("utf-8", errors="replace"),
            sha=data["sha"],
        )

    def _get_json(self, path: str, params: dict[str, str] | None = None):
        response = self._client.get(f"{self._api_base}{path}", params=params)
        response.raise_for_status()
        return response.json()


def _validate_public_http_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError("only http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise ProviderError("credential-bearing URLs are forbidden")
    if not parsed.hostname:
        raise ProviderError("URL hostname is required")
    hostname = parsed.hostname.lower()
    if hostname in {"localhost"} or hostname.endswith(".localhost"):
        raise ProviderError("localhost URLs are forbidden")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return url
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ProviderError("private or non-public IP URLs are forbidden")
    return url
