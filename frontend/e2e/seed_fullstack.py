"""Create the smallest durable run used by the real-browser confirmation smoke."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.research_intent_summary import (
    ConfirmedTaskParameters,
    ResearchIntentSummary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.task_bridge import TaskBridge
from autoad_researcher.repository_intelligence.acquisition import RepositoryAttestation
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.schemas.decisions import ConfirmedDecision
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.task_workspace.task_profile import create_task_profile
from autoad_researcher.ui.sources import append_source_ref
from autoad_researcher.worker.main import _process_pending_jobs


RUN_ID = "run_fullstack_e2e"
REPORT_RUN_ID = "run_report_fullstack_e2e"
REPOSITORY_SOURCE_ID = "repo_micro"


def main() -> None:
    runs_root_text = os.environ.get("AUTOAD_E2E_RUNS_ROOT")
    if not runs_root_text:
        raise SystemExit("AUTOAD_E2E_RUNS_ROOT is required")
    runs_root = Path(runs_root_text)
    now = datetime.now(timezone.utc)
    _seed_report_run(runs_root, created_at=now - timedelta(minutes=1))
    run_dir = runs_root / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=False)
    create_task_profile(
        run_dir=run_dir,
        run_id=RUN_ID,
        task_title="真实浏览器确认",
        created_at=now,
    )
    append_source_ref(
        run_dir,
        source_id="repo_official",
        kind="github_repo",
        user_label="official reference only",
        stored_path="repos/repo_official",
        status="parsed",
        intake_status="ok",
    )
    append_source_ref(
        run_dir,
        source_id=REPOSITORY_SOURCE_ID,
        kind="local_repo",
        user_label="05_RareCLIP_micro_repo.zip",
        stored_path=f"repos/{REPOSITORY_SOURCE_ID}",
        status="parsed",
        intake_status="ok",
    )
    repository = run_dir / "repos" / REPOSITORY_SOURCE_ID
    repository.mkdir(parents=True)
    (repository / "run.py").write_text("print('fixture')\n", encoding="utf-8")
    (repository / "evaluation.py").write_text("# protected fixture\n", encoding="utf-8")
    (repository / "autoad_executor_adapter.json").write_text(
        json.dumps(
            {
                "adapter_id": "generic_python",
                "entrypoint": "run.py",
                "smoke_argv": [sys.executable, "run.py"],
                "metrics_output": "metrics.json",
                "allowed_paths": ["run.py"],
                "protected_paths": ["evaluation.py"],
                "activation_evidence": "observed",
            }
        ),
        encoding="utf-8",
    )
    attestation = RepositoryAttestation(
        schema_version=1,
        source_id=REPOSITORY_SOURCE_ID,
        repository_root_label=f"local/{REPOSITORY_SOURCE_ID}",
        canonical_remote_url=None,
        head_commit=None,
        git_tree_sha=None,
        tree_sha="b" * 64,
        detached_head=None,
        dirty=False,
        git_status_porcelain="",
        symbolic_ref=None,
        submodule_declarations=[],
        tool_call_ids=["tool_local_tree_fingerprint"],
    )
    attestation_path = run_dir / "repo_acquisition" / REPOSITORY_SOURCE_ID / "repository_attestation.json"
    attestation_path.parent.mkdir(parents=True)
    attestation_path.write_text(attestation.model_dump_json(), encoding="utf-8")
    save_research_intent_summary(
        run_dir,
        ResearchIntentSummary(
            goal="验证明确的执行仓库绑定",
            confirmed_task_parameters=ConfirmedTaskParameters(
                primary_metrics=[
                    ConfirmedDecision(
                        value="image_auroc",
                        source="user_confirmed",
                        evidence="full-stack browser fixture",
                    )
                ]
            ),
        ),
    )
    TaskBridge.build_experiment_task(run_dir, user_input="确认 micro repo 作为执行仓库")


def _seed_report_run(runs_root: Path, *, created_at: datetime) -> None:
    run_dir = runs_root / REPORT_RUN_ID
    run_dir.mkdir(parents=True, exist_ok=False)
    create_task_profile(
        run_dir=run_dir,
        run_id=REPORT_RUN_ID,
        task_title="真实报告全栈验收",
        created_at=created_at,
    )
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="f" * 64,
        execution_mode="approve_each_step",
    )
    result, _ = ReportRequestService().request(
        run_dir,
        session_id=session.session_id,
    )
    for _ in range(10):
        if _process_pending_jobs(run_dir) == 0:
            break
    report_id = result["manifest"].report_id
    state = ReportStore().load_state(run_dir, report_id)
    if state.generation_status != "content_ready" or state.format_status.bundle != "ready":
        raise RuntimeError("full-stack report fixture did not become content_ready with a bundle")
    if not load_pipeline_jobs(run_dir):
        raise RuntimeError("full-stack report fixture did not persist report jobs")


if __name__ == "__main__":
    main()
