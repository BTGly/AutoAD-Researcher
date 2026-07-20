"""Tests for deferred Web and GitHub providers."""

import base64
import httpx
import pytest

from autoad_researcher.tools import (
    GitHubReadProvider,
    ProviderError,
    RecordedWebSearchProvider,
    SecureWebFetchProvider,
    WebSearchResult,
)

COMMIT = "a" * 40


def test_secure_web_fetch_rejects_private_and_credential_urls():
    provider = SecureWebFetchProvider(client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))))

    with pytest.raises(ProviderError, match="private"):
        provider.fetch("http://127.0.0.1/data")

    with pytest.raises(ProviderError, match="credential"):
        provider.fetch("https://user:token@example.com/data")


def test_secure_web_fetch_hashes_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="hello")

    provider = SecureWebFetchProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.fetch("https://example.com/page")

    assert result.status_code == 200
    assert result.content == "hello"
    assert result.content_bytes == b"hello"
    assert result.content_type == "text/plain; charset=utf-8"
    assert len(result.content_sha256) == 64


def test_secure_web_fetch_validates_redirect_location():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/metadata"})

    provider = SecureWebFetchProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="private"):
        provider.fetch("https://example.com/redirect")


def test_recorded_web_search_provider_returns_fixture_results():
    provider = RecordedWebSearchProvider(
        {"query": [WebSearchResult(title="Repo", url="https://github.com/example/repo", snippet="official")]}
    )

    assert provider.search("query")[0].title == "Repo"
    assert provider.search("missing") == []


def test_github_read_provider_reads_metadata_commit_and_file():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/example/repo":
            return httpx.Response(
                200,
                json={
                    "owner": {"login": "example"},
                    "name": "repo",
                    "default_branch": "main",
                    "fork": False,
                    "archived": False,
                    "html_url": "https://github.com/example/repo",
                },
            )
        if path == "/repos/example/repo/commits/main":
            return httpx.Response(200, json={"sha": COMMIT})
        if path == "/repos/example/repo/contents/README.md":
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(b"# Repo\n").decode("ascii"),
                    "sha": "blob-sha",
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    provider = GitHubReadProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_base="https://api.github.test",
    )

    metadata = provider.repository_metadata("example", "repo")
    commit = provider.commit_ref("example", "repo", "main")
    file_content = provider.file_text("example", "repo", "README.md", "main")

    assert metadata.default_branch == "main"
    assert commit.sha == COMMIT
    assert file_content.text == "# Repo\n"
