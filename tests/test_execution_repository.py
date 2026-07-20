from __future__ import annotations

import json
import sys
from pathlib import Path

from autoad_researcher.assistant.v2.execution_repository import (
    assign_execution_repository_role,
    resolve_execution_repository,
)
from autoad_researcher.repository_intelligence.acquisition import RepositoryAttestation
from autoad_researcher.schemas.decisions import ConfirmedDecision
from autoad_researcher.ui.sources import append_source_ref


def _authorization(source_id: str) -> ConfirmedDecision:
    return ConfirmedDecision(
        value=source_id,
        source="user_confirmed",
        evidence=f"用户明确指定 {source_id} 作为可执行仓库",
    )


def _write_acquired_repository(run_dir: Path, source_id: str) -> None:
    repository = run_dir / "repos" / source_id
    repository.mkdir(parents=True)
    (repository / "run_experiment.py").write_text("print('ok')\n", encoding="utf-8")
    (repository / "evaluation.py").write_text("", encoding="utf-8")
    (repository / "autoad_executor_adapter.json").write_text(
        json.dumps({
            "adapter_id": "generic_python",
            "entrypoint": "run_experiment.py",
            "smoke_argv": [sys.executable, "run_experiment.py"],
            "metrics_output": "metrics.json",
            "allowed_paths": ["run_experiment.py"],
            "protected_paths": ["evaluation.py", "autoad_executor_adapter.json"],
            "activation_evidence": "observed",
        }),
        encoding="utf-8",
    )
    attestation = RepositoryAttestation(
        schema_version=1,
        source_id=source_id,
        repository_root_label=f"local/{source_id}",
        canonical_remote_url=None,
        head_commit=None,
        git_tree_sha=None,
        tree_sha="a" * 64,
        detached_head=None,
        dirty=False,
        git_status_porcelain="",
        symbolic_ref=None,
        submodule_declarations=[],
        tool_call_ids=["tool_local_tree_fingerprint"],
    )
    path = run_dir / "repo_acquisition" / source_id / "repository_attestation.json"
    path.parent.mkdir(parents=True)
    path.write_text(attestation.model_dump_json(indent=2), encoding="utf-8")


def _append_repository(run_dir: Path, source_id: str, *, kind: str) -> None:
    append_source_ref(
        run_dir,
        source_id=source_id,
        kind=kind,  # type: ignore[arg-type]
        user_label=source_id,
        stored_path=f"repos/{source_id}",
        status="parsed",
        intake_status="ok",
    )


def test_resolver_uses_only_explicit_executable_repository(tmp_path: Path):
    _append_repository(tmp_path, "src_reference", kind="github_repo")
    _append_repository(tmp_path, "src_candidate", kind="local_repo")
    _write_acquired_repository(tmp_path, "src_reference")
    _write_acquired_repository(tmp_path, "src_candidate")
    assign_execution_repository_role(
        tmp_path,
        source_id="src_reference",
        role="reference_only",
        authorization=_authorization("src_reference"),
    )
    assign_execution_repository_role(
        tmp_path,
        source_id="src_candidate",
        role="executable",
        authorization=_authorization("src_candidate"),
    )

    admitted = resolve_execution_repository(tmp_path)

    assert admitted.status == "admitted"
    assert admitted.binding is not None
    assert admitted.binding.source_id == "src_candidate"
    assert admitted.binding.repository_ref == "repos/src_candidate"
    assert admitted.binding.adapter_id == "generic_python"


def test_resolver_never_promotes_acquired_reference_without_explicit_role(tmp_path: Path):
    _append_repository(tmp_path, "src_reference", kind="github_repo")
    _write_acquired_repository(tmp_path, "src_reference")

    blocked = resolve_execution_repository(tmp_path)

    assert blocked.status == "blocked"
    assert blocked.code == "execution_repository_unresolved"


def test_resolver_rejects_unsupported_executable_repository(tmp_path: Path):
    _append_repository(tmp_path, "src_candidate", kind="local_repo")
    _write_acquired_repository(tmp_path, "src_candidate")
    (tmp_path / "repos" / "src_candidate" / "autoad_executor_adapter.json").unlink()
    assign_execution_repository_role(
        tmp_path,
        source_id="src_candidate",
        role="executable",
        authorization=_authorization("src_candidate"),
    )

    blocked = resolve_execution_repository(tmp_path)

    assert blocked.status == "blocked"
    assert blocked.code == "execution_adapter_unsupported"


def test_resolver_rejects_multiple_explicit_execution_targets(tmp_path: Path):
    for source_id in ("src_one", "src_two"):
        _append_repository(tmp_path, source_id, kind="local_repo")
        _write_acquired_repository(tmp_path, source_id)
        assign_execution_repository_role(
            tmp_path,
            source_id=source_id,
            role="executable",
            authorization=_authorization(source_id),
        )

    blocked = resolve_execution_repository(tmp_path)

    assert blocked.status == "blocked"
    assert blocked.code == "execution_repository_unresolved"


def test_role_assignment_rejects_non_repository_source(tmp_path: Path):
    append_source_ref(
        tmp_path,
        source_id="src_paper",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_paper/paper.pdf",
        status="uploaded_not_parsed",
    )

    try:
        assign_execution_repository_role(
            tmp_path,
            source_id="src_paper",
            role="executable",
            authorization=_authorization("src_paper"),
        )
    except ValueError as exc:
        assert str(exc) == "only registered repository sources can receive an execution role"
    else:
        raise AssertionError("non-repository source role assignment should fail")
