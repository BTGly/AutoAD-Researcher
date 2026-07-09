"""Repository acquisition and state attestation for Step 3.1 R6."""

import configparser
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.repository_intelligence.evidence import (
    append_evidence,
    create_repository_identity_evidence,
)
from autoad_researcher.repository_intelligence.ids import GitCommitPattern, IdentifierPattern, Sha256Pattern, validate_relative_path
from autoad_researcher.repository_intelligence.models import RepositorySource, SubmoduleRecord
from autoad_researcher.tools import (
    PermissionDecisionRecord,
    PermissionEngine,
    ProcessToolRequest,
    ProcessToolResult,
    append_permission_decision,
    default_repository_permission_engine,
    run_process_tool,
)

AcquisitionProfile = Literal["shallow_ref", "partial_exact", "generic_shallow", "local"]
AcquisitionStatus = Literal["success", "failed", "blocked"]

GIT_ACQUISITION_ALLOWED = {
    "init",
    "clone",
    "remote",
    "fetch",
    "checkout",
    "status",
    "rev-parse",
    "symbolic-ref",
}


class AcquisitionError(ValueError):
    """Raised when acquisition inputs or policy are invalid."""


class RepositoryAcquisitionRequest(BaseModel):
    """Input for acquiring a fixed repository source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    workspace_root: Path
    remote_url: str | None = None
    local_path: Path | None = None
    resolved_ref: str | None = None
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    acquisition_profile: AcquisitionProfile

    @model_validator(mode="after")
    def _validate_profile_inputs(self):
        if self.acquisition_profile in {"shallow_ref", "partial_exact"}:
            if self.remote_url is None:
                raise ValueError("remote_url is required for remote acquisition")
            if self.resolved_commit is None:
                raise ValueError("resolved_commit is required for remote acquisition")
        if self.acquisition_profile == "generic_shallow" and self.remote_url is None:
            raise ValueError("remote_url is required for generic_shallow acquisition")
        if self.acquisition_profile == "shallow_ref" and self.resolved_ref is None:
            raise ValueError("resolved_ref is required for shallow_ref acquisition")
        if self.acquisition_profile == "local" and self.local_path is None:
            raise ValueError("local_path is required for local acquisition")
        return self


class RepositoryAttestation(BaseModel):
    """Non-LLM repository state attestation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    repository_root_label: str
    canonical_remote_url: str | None = None
    head_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    git_tree_sha: str | None = Field(default=None, pattern=GitCommitPattern)
    tree_sha: str = Field(pattern=Sha256Pattern)
    detached_head: bool | None
    dirty: bool
    git_status_porcelain: str
    symbolic_ref: str | None = None
    submodule_declarations: list[SubmoduleRecord] = Field(default_factory=list)
    tool_call_ids: list[str] = Field(min_length=1)

    @field_validator("repository_root_label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        return validate_relative_path(value)

    @property
    def attestation_sha256(self) -> str:
        return canonical_sha256(self)


class AcquisitionToolCallRecord(BaseModel):
    """Auditable git process call used by acquisition or attestation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    tool_call_id: str = Field(pattern=IdentifierPattern)
    argv: list[str]
    cwd_label: str
    status: str
    exit_code: int | None = None
    stdout_sha256: str
    stderr_sha256: str


class RepositoryAcquisitionResult(BaseModel):
    """Acquisition result envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    status: AcquisitionStatus
    source: RepositorySource | None = None
    attestation: RepositoryAttestation | None = None
    error_code: str | None = None
    error_message: str | None = None
    tool_calls: list[AcquisitionToolCallRecord] = Field(default_factory=list)


class RepositoryAcquisitionRunner:
    """Controlled Git acquisition runner backed by argv-based ProcessTool."""

    def __init__(self, *, permission_engine: PermissionEngine | None = None, timeout_seconds: int = 30):
        self.permission_engine = permission_engine or default_repository_permission_engine()
        self.timeout_seconds = timeout_seconds

    def acquire(self, request: RepositoryAcquisitionRequest, *, run_dir: Path) -> RepositoryAcquisitionResult:
        """Acquire a remote or local source and write R6 artifacts."""
        tool_calls: list[AcquisitionToolCallRecord] = []
        decisions_path = run_dir / "acquisition_permission_decisions.jsonl"
        calls_path = run_dir / "acquisition_tool_calls.jsonl"
        evidence_index_path = run_dir / "evidence_index.jsonl"
        run_dir.mkdir(parents=True, exist_ok=True)

        try:
            workspace_root = request.workspace_root.resolve()
            workspace_root.mkdir(parents=True, exist_ok=True)
            if request.acquisition_profile == "local":
                source, attestation = attest_local_source(
                    request=request,
                    run_dir=run_dir,
                    permission_engine=self.permission_engine,
                    timeout_seconds=self.timeout_seconds,
                    tool_calls=tool_calls,
                    decisions_path=decisions_path,
                    calls_path=calls_path,
                )
            else:
                _assert_safe_remote_url(request.remote_url)
                _assert_git_profile_is_safe(request.acquisition_profile, request.resolved_ref, request.resolved_commit)
                target_label = f"repos/{request.source_id}"
                target = _resolve_workspace_child(workspace_root, target_label)
                _ensure_empty_target(target)
                template_dir = _controlled_template_dir(workspace_root)
                target.mkdir(parents=True, exist_ok=True)

                if request.acquisition_profile == "shallow_ref":
                    source, attestation = self._acquire_shallow_ref(
                        request=request,
                        target=target,
                        target_label=target_label,
                        template_dir=template_dir,
                        run_dir=run_dir,
                        tool_calls=tool_calls,
                        decisions_path=decisions_path,
                        calls_path=calls_path,
                    )
                elif request.acquisition_profile == "generic_shallow":
                    source, attestation = self._acquire_generic_shallow(
                        request=request,
                        target=target,
                        target_label=target_label,
                        template_dir=template_dir,
                        run_dir=run_dir,
                        tool_calls=tool_calls,
                        decisions_path=decisions_path,
                        calls_path=calls_path,
                    )
                else:
                    source, attestation = self._acquire_partial_exact(
                        request=request,
                        target=target,
                        target_label=target_label,
                        template_dir=template_dir,
                        run_dir=run_dir,
                        tool_calls=tool_calls,
                        decisions_path=decisions_path,
                        calls_path=calls_path,
                    )

            _write_json_atomic(run_dir / "repository_source.json", source)
            _write_json_atomic(run_dir / "repository_attestation.json", attestation)
            append_evidence(
                evidence_index_path,
                create_repository_identity_evidence(
                    source=source,
                    evidence_id="ev_repository_identity_001",
                    attestation_sha256=attestation.attestation_sha256,
                    tool_call_ids=attestation.tool_call_ids,
                ),
            )
            return RepositoryAcquisitionResult(
                schema_version=1,
                status="success",
                source=source,
                attestation=attestation,
                tool_calls=tool_calls,
            )
        except AcquisitionError as exc:
            return RepositoryAcquisitionResult(
                schema_version=1,
                status="failed",
                error_code="ACQUISITION_FAILED",
                error_message=str(exc),
                tool_calls=tool_calls,
            )

    def _acquire_shallow_ref(
        self,
        *,
        request: RepositoryAcquisitionRequest,
        target: Path,
        target_label: str,
        template_dir: Path,
        run_dir: Path,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> tuple[RepositorySource, RepositoryAttestation]:
        assert request.remote_url is not None
        assert request.resolved_ref is not None
        assert request.resolved_commit is not None

        self._run_git(target, target_label, ["git", "init", f"--template={template_dir}"], "tool_git_init", tool_calls, decisions_path, calls_path)
        self._run_git(target, target_label, ["git", "remote", "add", "origin", request.remote_url], "tool_git_remote_add", tool_calls, decisions_path, calls_path)
        self._run_git(
            target,
            target_label,
            ["git", "fetch", "--depth=1", "--no-tags", "origin", request.resolved_ref],
            "tool_git_fetch",
            tool_calls,
            decisions_path,
            calls_path,
        )
        fetch_head = self._run_git_stdout(target, target_label, ["git", "rev-parse", "FETCH_HEAD"], "tool_git_rev_parse_fetch_head", tool_calls, decisions_path, calls_path)
        if fetch_head != request.resolved_commit:
            raise AcquisitionError("SOURCE_REF_MOVED: FETCH_HEAD does not match resolved_commit")
        self._run_git(target, target_label, ["git", "checkout", "--detach", "FETCH_HEAD"], "tool_git_checkout", tool_calls, decisions_path, calls_path)
        head = self._run_git_stdout(target, target_label, ["git", "rev-parse", "HEAD"], "tool_git_rev_parse_head", tool_calls, decisions_path, calls_path)
        if head != request.resolved_commit:
            raise AcquisitionError("HEAD does not match resolved_commit after checkout")
        return attest_repository(
            source_id=request.source_id,
            repository_root=target,
            repository_root_label=target_label,
            canonical_remote_url=request.remote_url,
            requested_ref=request.resolved_ref,
            acquisition_profile="shallow_ref",
            permission_engine=self.permission_engine,
            timeout_seconds=self.timeout_seconds,
            tool_calls=tool_calls,
            decisions_path=decisions_path,
            calls_path=calls_path,
        )

    def _acquire_generic_shallow(
        self,
        *,
        request: RepositoryAcquisitionRequest,
        target: Path,
        target_label: str,
        template_dir: Path,
        run_dir: Path,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> tuple[RepositorySource, RepositoryAttestation]:
        assert request.remote_url is not None
        self._run_git(
            request.workspace_root.resolve(),
            "workspace_root",
            [
                "git",
                "clone",
                "--depth=1",
                "--no-tags",
                f"--template={template_dir}",
                request.remote_url,
                str(target),
            ],
            "tool_git_clone",
            tool_calls,
            decisions_path,
            calls_path,
        )
        return attest_repository(
            source_id=request.source_id,
            repository_root=target,
            repository_root_label=target_label,
            canonical_remote_url=request.remote_url,
            requested_ref=request.resolved_ref,
            acquisition_profile="generic_shallow",
            permission_engine=self.permission_engine,
            timeout_seconds=self.timeout_seconds,
            tool_calls=tool_calls,
            decisions_path=decisions_path,
            calls_path=calls_path,
        )

    def _acquire_partial_exact(
        self,
        *,
        request: RepositoryAcquisitionRequest,
        target: Path,
        target_label: str,
        template_dir: Path,
        run_dir: Path,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> tuple[RepositorySource, RepositoryAttestation]:
        assert request.remote_url is not None
        assert request.resolved_commit is not None
        self._run_git(
            request.workspace_root.resolve(),
            "workspace_root",
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--no-tags",
                f"--template={template_dir}",
                request.remote_url,
                str(target),
            ],
            "tool_git_clone",
            tool_calls,
            decisions_path,
            calls_path,
        )
        try:
            self._run_git(
                target,
                target_label,
                ["git", "fetch", "--no-tags", "origin", request.resolved_commit],
                "tool_git_fetch_exact",
                tool_calls,
                decisions_path,
                calls_path,
            )
        except AcquisitionError as exc:
            raise AcquisitionError("SOURCE_COMMIT_MISMATCH: remote did not provide resolved_commit") from exc
        self._run_git(target, target_label, ["git", "checkout", "--detach", request.resolved_commit], "tool_git_checkout", tool_calls, decisions_path, calls_path)
        head = self._run_git_stdout(target, target_label, ["git", "rev-parse", "HEAD"], "tool_git_rev_parse_head", tool_calls, decisions_path, calls_path)
        if head != request.resolved_commit:
            raise AcquisitionError("SOURCE_COMMIT_MISMATCH: HEAD does not match resolved_commit after checkout")
        return attest_repository(
            source_id=request.source_id,
            repository_root=target,
            repository_root_label=target_label,
            canonical_remote_url=request.remote_url,
            requested_ref=request.resolved_commit,
            acquisition_profile="partial_exact",
            permission_engine=self.permission_engine,
            timeout_seconds=self.timeout_seconds,
            tool_calls=tool_calls,
            decisions_path=decisions_path,
            calls_path=calls_path,
        )

    def _run_git(
        self,
        cwd: Path,
        cwd_label: str,
        argv: list[str],
        tool_call_id: str,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> ProcessToolResult:
        _validate_git_argv(argv)
        result = run_process_tool(
            ProcessToolRequest(
                tool_call_id=tool_call_id,
                argv=argv,
                cwd=cwd,
                cwd_label=cwd_label,
                environment=_safe_git_environment(),
                timeout_seconds=self.timeout_seconds,
                stage="acquisition",
                permission_profile="repository_acquisition",
            ),
            permission_engine=self.permission_engine,
        )
        _record_process_result(result, argv, cwd_label, tool_calls, decisions_path, calls_path)
        if result.status != "success":
            stderr = result.output.stderr if result.output is not None else ""
            raise AcquisitionError(f"git command failed: {tool_call_id}: {result.status}: {stderr.strip()}")
        return result

    def _run_git_stdout(
        self,
        cwd: Path,
        cwd_label: str,
        argv: list[str],
        tool_call_id: str,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> str:
        result = self._run_git(cwd, cwd_label, argv, tool_call_id, tool_calls, decisions_path, calls_path)
        assert result.output is not None
        return result.output.stdout.strip()


def attest_local_source(
    *,
    request: RepositoryAcquisitionRequest,
    run_dir: Path,
    permission_engine: PermissionEngine,
    timeout_seconds: int,
    tool_calls: list[AcquisitionToolCallRecord],
    decisions_path: Path,
    calls_path: Path,
) -> tuple[RepositorySource, RepositoryAttestation]:
    assert request.local_path is not None
    local_path = request.local_path.resolve()
    if not local_path.exists():
        raise AcquisitionError("local_path does not exist")
    label = f"local/{request.source_id}"
    if (local_path / ".git").exists():
        return attest_repository(
            source_id=request.source_id,
            repository_root=local_path,
            repository_root_label=label,
            canonical_remote_url=None,
            requested_ref=request.resolved_ref,
            acquisition_profile="local",
            permission_engine=permission_engine,
            timeout_seconds=timeout_seconds,
            tool_calls=tool_calls,
            decisions_path=decisions_path,
            calls_path=calls_path,
        )
    tree_sha = _non_git_tree_sha(local_path)
    attestation = RepositoryAttestation(
        schema_version=1,
        source_id=request.source_id,
        repository_root_label=label,
        canonical_remote_url=None,
        head_commit=None,
        git_tree_sha=None,
        tree_sha=tree_sha,
        detached_head=None,
        dirty=False,
        git_status_porcelain="",
        symbolic_ref=None,
        submodule_declarations=[],
        tool_call_ids=["tool_local_tree_fingerprint"],
    )
    source = RepositorySource(
        schema_version=1,
        source_id=request.source_id,
        kind="local_workspace",
        canonical_remote_url=None,
        requested_ref=request.resolved_ref,
        acquisition_profile="local",
        resolved_commit=None,
        tree_sha=tree_sha,
        detached_head=None,
        dirty=False,
        local_path_label=label,
        submodule_declarations=[],
        source_fingerprint=attestation.attestation_sha256,
    )
    return source, attestation


def attest_repository(
    *,
    source_id: str,
    repository_root: Path,
    repository_root_label: str,
    canonical_remote_url: str | None,
    requested_ref: str | None,
    acquisition_profile: AcquisitionProfile,
    permission_engine: PermissionEngine,
    timeout_seconds: int,
    tool_calls: list[AcquisitionToolCallRecord],
    decisions_path: Path,
    calls_path: Path,
) -> tuple[RepositorySource, RepositoryAttestation]:
    runner = _GitProbeRunner(permission_engine=permission_engine, timeout_seconds=timeout_seconds)
    head = runner.run(repository_root, repository_root_label, ["git", "rev-parse", "HEAD"], "tool_git_attest_head", tool_calls, decisions_path, calls_path).strip()
    git_tree = runner.run(repository_root, repository_root_label, ["git", "rev-parse", "HEAD^{tree}"], "tool_git_attest_tree", tool_calls, decisions_path, calls_path).strip()
    status = runner.run(repository_root, repository_root_label, ["git", "status", "--porcelain"], "tool_git_attest_status", tool_calls, decisions_path, calls_path)
    remote = canonical_remote_url
    remote_result = runner.run_allow_failure(repository_root, repository_root_label, ["git", "remote", "get-url", "origin"], "tool_git_attest_remote", tool_calls, decisions_path, calls_path)
    if remote_result.status == "success" and remote_result.output is not None:
        remote = remote_result.output.stdout.strip() or remote
    symbolic_result = runner.run_allow_failure(repository_root, repository_root_label, ["git", "symbolic-ref", "-q", "HEAD"], "tool_git_attest_symbolic_ref", tool_calls, decisions_path, calls_path)
    symbolic_ref = None
    if symbolic_result.status == "success" and symbolic_result.output is not None:
        symbolic_ref = symbolic_result.output.stdout.strip() or None
    detached_head = symbolic_ref is None
    submodules = _parse_gitmodules(repository_root / ".gitmodules")
    tree_sha = hashlib.sha256(git_tree.encode("utf-8")).hexdigest()
    attestation = RepositoryAttestation(
        schema_version=1,
        source_id=source_id,
        repository_root_label=repository_root_label,
        canonical_remote_url=remote,
        head_commit=head,
        git_tree_sha=git_tree,
        tree_sha=tree_sha,
        detached_head=detached_head,
        dirty=bool(status.strip()),
        git_status_porcelain=status,
        symbolic_ref=symbolic_ref,
        submodule_declarations=submodules,
        tool_call_ids=[record.tool_call_id for record in tool_calls],
    )
    source = RepositorySource(
        schema_version=1,
        source_id=source_id,
        kind="local_workspace" if acquisition_profile == "local" else "github_public",
        canonical_remote_url=remote,
        requested_ref=requested_ref,
        acquisition_profile=acquisition_profile,
        resolved_commit=head,
        tree_sha=tree_sha,
        detached_head=detached_head,
        dirty=bool(status.strip()),
        local_path_label=repository_root_label,
        submodule_declarations=submodules,
        source_fingerprint=attestation.attestation_sha256,
    )
    return source, attestation


class _GitProbeRunner:
    def __init__(self, *, permission_engine: PermissionEngine, timeout_seconds: int):
        self.permission_engine = permission_engine
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        cwd: Path,
        cwd_label: str,
        argv: list[str],
        tool_call_id: str,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> str:
        result = self.run_allow_failure(cwd, cwd_label, argv, tool_call_id, tool_calls, decisions_path, calls_path)
        if result.status != "success":
            stderr = result.output.stderr if result.output is not None else ""
            raise AcquisitionError(f"git probe failed: {tool_call_id}: {result.status}: {stderr.strip()}")
        assert result.output is not None
        return result.output.stdout

    def run_allow_failure(
        self,
        cwd: Path,
        cwd_label: str,
        argv: list[str],
        tool_call_id: str,
        tool_calls: list[AcquisitionToolCallRecord],
        decisions_path: Path,
        calls_path: Path,
    ) -> ProcessToolResult:
        _validate_git_argv(argv)
        result = run_process_tool(
            ProcessToolRequest(
                tool_call_id=tool_call_id,
                argv=argv,
                cwd=cwd,
                cwd_label=cwd_label,
                environment=_safe_git_environment(),
                timeout_seconds=self.timeout_seconds,
                stage="acquisition",
                permission_profile="repository_acquisition",
            ),
            permission_engine=self.permission_engine,
        )
        _record_process_result(result, argv, cwd_label, tool_calls, decisions_path, calls_path)
        return result


def _validate_git_argv(argv: list[str]) -> None:
    if not argv or argv[0] != "git":
        raise AcquisitionError("only git argv commands are allowed")
    if len(argv) < 2:
        raise AcquisitionError("git subcommand is required")
    subcommand = argv[1]
    if subcommand not in GIT_ACQUISITION_ALLOWED:
        raise AcquisitionError(f"git subcommand not allowed during acquisition: {subcommand}")
    if "submodule" in argv or "lfs" in argv:
        raise AcquisitionError("git submodule and lfs operations are forbidden")
    if subcommand == "config":
        raise AcquisitionError("git config is forbidden")
    if "--filter=blob:none" in argv and any(arg.startswith("--depth") for arg in argv):
        raise AcquisitionError("--filter and --depth must not be mixed")


def _assert_git_profile_is_safe(profile: AcquisitionProfile, resolved_ref: str | None, resolved_commit: str | None) -> None:
    if profile == "shallow_ref" and resolved_ref is None:
        raise AcquisitionError("shallow_ref requires resolved_ref")
    if profile == "partial_exact" and resolved_commit is None:
        raise AcquisitionError("partial_exact requires resolved_commit")


def _assert_safe_remote_url(remote_url: str | None) -> None:
    if remote_url is None:
        raise AcquisitionError("remote_url is required")
    parsed = urlsplit(remote_url)
    if parsed.username or parsed.password:
        raise AcquisitionError("credential-bearing remote URLs are forbidden")


def _safe_git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
    }


def _controlled_template_dir(workspace_root: Path) -> Path:
    template = workspace_root / ".autoad-empty-git-template"
    template.mkdir(parents=True, exist_ok=True)
    return template


def _resolve_workspace_child(workspace_root: Path, label: str) -> Path:
    safe_label = validate_relative_path(label)
    candidate = (workspace_root / safe_label).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise AcquisitionError(f"workspace path escape: {label}") from exc
    return candidate


def _ensure_empty_target(target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        raise AcquisitionError(f"target repository directory is not empty: {target}")


def _record_process_result(
    result: ProcessToolResult,
    argv: list[str],
    cwd_label: str,
    tool_calls: list[AcquisitionToolCallRecord],
    decisions_path: Path,
    calls_path: Path,
) -> None:
    append_permission_decision(decisions_path, result.permission)
    output = result.output
    record = AcquisitionToolCallRecord(
        schema_version=1,
        tool_call_id=result.permission.tool_call_id,
        argv=argv,
        cwd_label=cwd_label,
        status=result.status,
        exit_code=None if output is None else output.exit_code,
        stdout_sha256=hashlib.sha256((output.stdout if output is not None else "").encode("utf-8")).hexdigest(),
        stderr_sha256=hashlib.sha256((output.stderr if output is not None else "").encode("utf-8")).hexdigest(),
    )
    tool_calls.append(record)
    _append_jsonl(calls_path, record)


def _parse_gitmodules(path: Path) -> list[SubmoduleRecord]:
    if not path.exists():
        return []
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    records: list[SubmoduleRecord] = []
    for section in parser.sections():
        records.append(SubmoduleRecord(name=section, **dict(parser.items(section))))
    return records


def _non_git_tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        h.update(relative.encode("utf-8") + b"\0")
        h.update(sha256_file(path).encode("utf-8") + b"\0")
    return h.hexdigest()


def _write_json_atomic(path: Path, value: BaseModel) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True)
    with path.open("ab") as f:
        f.write(data.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())
