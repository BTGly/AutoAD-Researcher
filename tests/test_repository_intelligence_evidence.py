"""Tests for Repository Intelligence evidence middleware."""

import hashlib
from pathlib import Path

import pytest

from autoad_researcher.repository_intelligence import (
    ActiveRepositoryContext,
    EvidenceMiddlewareError,
    FileEvidenceRequest,
    RepositorySource,
    WebEvidenceRef,
    append_evidence,
    create_file_evidence,
    create_repository_identity_evidence,
    read_evidence_index,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
COMMIT = "a" * 40


def context(repo: Path) -> ActiveRepositoryContext:
    return ActiveRepositoryContext(
        source_id="source_001",
        repository_root=repo,
        resolved_commit=COMMIT,
        tree_sha=SHA_A,
    )


def repository_source() -> RepositorySource:
    return RepositorySource(
        schema_version=1,
        source_id="source_001",
        kind="github_public",
        canonical_remote_url="https://github.com/example/repo",
        requested_ref="main",
        acquisition_profile="shallow_ref",
        resolved_commit=COMMIT,
        tree_sha=SHA_A,
        detached_head=True,
        dirty=False,
        local_path_label="workspace/repos/source_001",
        source_fingerprint=SHA_B,
    )


def test_create_file_evidence_maps_relative_path_and_hashes_snippet(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("one\nalpha\nbeta\nfour\n", encoding="utf-8")

    evidence = create_file_evidence(
        context=context(repo),
        request=FileEvidenceRequest(
            evidence_id="ev_file",
            path="src/main.py",
            start_line=2,
            end_line=3,
            tool_call_id="tool_001",
        ),
    )

    assert evidence.path == "src/main.py"
    assert evidence.repository_commit == COMMIT
    assert evidence.file_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert evidence.snippet_sha256 == hashlib.sha256(b"alpha\nbeta\n").hexdigest()


def test_file_evidence_rejects_path_escape(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="parent traversal forbidden"):
        FileEvidenceRequest(
            evidence_id="ev_file",
            path="../outside.py",
            start_line=1,
            end_line=1,
            tool_call_id="tool_001",
        )


def test_file_evidence_rejects_symlink_component(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret\n", encoding="utf-8")
    (repo / "link").symlink_to(outside)

    with pytest.raises(EvidenceMiddlewareError, match="symlink path forbidden"):
        create_file_evidence(
            context=context(repo),
            request=FileEvidenceRequest(
                evidence_id="ev_file",
                path="link/secret.py",
                start_line=1,
                end_line=1,
                tool_call_id="tool_001",
            ),
        )


def test_identity_evidence_enters_append_only_index(tmp_path: Path):
    evidence = create_repository_identity_evidence(
        source=repository_source(),
        evidence_id="ev_identity",
        attestation_sha256=SHA_A,
        tool_call_ids=["tool_git"],
    )
    index = tmp_path / "evidence_index.jsonl"

    append_evidence(index, evidence)
    records = read_evidence_index(index)

    assert len(records) == 1
    assert records[0].evidence.source_kind == "repository_identity"
    assert records[0].evidence.evidence_id == "ev_identity"

    with pytest.raises(EvidenceMiddlewareError, match="duplicate evidence_id"):
        append_evidence(index, evidence)


def test_evidence_index_rejects_env_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = repo / ".env"
    env_file.write_text("TOKEN=secret\n", encoding="utf-8")
    evidence = create_file_evidence(
        context=context(repo),
        request=FileEvidenceRequest(
            evidence_id="ev_env",
            path=".env",
            start_line=1,
            end_line=1,
            tool_call_id="tool_001",
        ),
    )

    with pytest.raises(EvidenceMiddlewareError, match=".env paths"):
        append_evidence(tmp_path / "evidence_index.jsonl", evidence)


def test_evidence_index_rejects_credential_url(tmp_path: Path):
    evidence = WebEvidenceRef(
        source_kind="web_page",
        evidence_id="ev_web",
        url="https://user:token@example.com/repo",
        content_sha256=SHA_A,
        tool_call_id="tool_web",
        trust_level="association_lead",
    )

    with pytest.raises(EvidenceMiddlewareError, match="credential-bearing URLs"):
        append_evidence(tmp_path / "evidence_index.jsonl", evidence)
