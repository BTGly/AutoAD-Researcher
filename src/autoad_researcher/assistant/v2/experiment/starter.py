"""Idempotent bridge from a confirmed V2 task to the experiment control plane."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from autoad_researcher.assistant.model_routing import ModelRoute, select_model_route
from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.job_service import create_or_get_pipeline_job
from autoad_researcher.assistant.v2.task_bridge import ExperimentTaskDraft
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.session import ExecutionMode, ExperimentSession
from autoad_researcher.experiment.session_store import ExperimentSessionStore

ENVIRONMENT_JOB_TYPE = "experiment_environment_prepare"


class ExperimentStartResult(BaseModel):
    """Session and durable environment Job returned by a confirm replay."""

    model_config = ConfigDict(extra="forbid")

    session: ExperimentSession
    environment_job: dict[str, object]
    disposition: Literal["created", "repaired", "reused"]


class ExperimentStarter:
    """Create or repair one Session and one revision-specific environment Job."""

    def __init__(self, session_store: ExperimentSessionStore | None = None):
        self._session_store = session_store or ExperimentSessionStore()

    def on_task_confirmed(
        self,
        run_dir: Path,
        confirmed_task: ExperimentTaskDraft,
        *,
        execution_mode: ExecutionMode,
        model_route: ModelRoute | None = None,
    ) -> ExperimentStartResult:
        binding = confirmed_task.execution_repository_binding
        if execution_mode != "plan_only" and binding is None:
            raise ValueError("confirmed execution task is missing an execution repository binding")
        task_hash = canonical_sha256({
            "input_task": confirmed_task.input_task.model_dump(mode="json"),
            "execution_repository_binding": (
                binding.model_dump(mode="json") if binding is not None else None
            ),
        })
        selected_route = model_route or select_model_route(
            "experiment_agent",
            os.environ.get("AUTOAD_EXPERIMENT_MODEL", "").strip() or None,
        )
        append_event(
            run_dir,
            "experiment.start_requested",
            {"task_id": confirmed_task.task_id, "task_hash": task_hash, "execution_mode": execution_mode},
        )
        session, session_created = self._session_store.create_or_get(
            run_dir,
            task_ref="input_task.yaml",
            task_hash=task_hash,
            execution_mode=execution_mode,
            repository_ref=binding.repository_ref if binding is not None else None,
            execution_repository_binding_ref=(
                "task_bridge/execution_repository_binding.json"
                if binding is not None else None
            ),
            execution_repository_binding_sha256=(
                canonical_sha256(binding) if binding is not None else None
            ),
            model_route=selected_route.snapshot(),
        )
        if session_created:
            append_event(
                run_dir,
                "experiment.session.created",
                {"session_id": session.session_id, "task_hash": task_hash},
            )
            append_event(
                run_dir,
                "experiment.authorization.confirmed",
                {"session_id": session.session_id, "execution_mode": execution_mode},
            )
        else:
            session, authorization_changed = self._session_store.update_authorization(
                run_dir,
                session_id=session.session_id,
                execution_mode=execution_mode,
            )
            append_event(
                run_dir,
                "experiment.session.reused",
                {"session_id": session.session_id, "task_hash": task_hash},
            )
            if authorization_changed:
                append_event(
                    run_dir,
                    "experiment.authorization.changed",
                    {
                        "session_id": session.session_id,
                        "execution_mode": execution_mode,
                        "authorization_revision": session.authorization_revision,
                    },
                )

        payload = {
            "session_id": session.session_id,
            "task_ref": session.task_ref,
            "environment_revision": session.environment_revision,
            "execution_repository_binding_ref": session.execution_repository_binding_ref,
            "execution_repository_binding_sha256": session.execution_repository_binding_sha256,
            "model_route": session.model_route,
        }
        job, job_created = create_or_get_pipeline_job(
            run_dir,
            source_id=session.session_id,
            job_type=ENVIRONMENT_JOB_TYPE,
            idempotency_key=(
                f"environment_prepare:{session.session_id}:r{session.environment_revision}"
            ),
            evidence_role="experiment_environment_prepare",
            payload=payload,
        )
        if job_created:
            append_event(
                run_dir,
                "experiment.environment_prepare.queued",
                {"session_id": session.session_id, "job_id": job["job_id"], "revision": session.environment_revision},
            )

        disposition: Literal["created", "repaired", "reused"]
        if session_created:
            disposition = "created"
        elif job_created:
            disposition = "repaired"
        else:
            disposition = "reused"
        return ExperimentStartResult(
            session=session,
            environment_job=job,
            disposition=disposition,
        )
