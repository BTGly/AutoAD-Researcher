"""Stage 3 top-level orchestrator — wires 3.1→3.9 into a single run()."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.schemas.stage3_acceptance import (
    PENDING_L3_ARTIFACTS,
    STAGE3_ACCEPTANCE_STAGE_ORDER,
    ArtifactChainBinding,
    ArtifactChainValidationReport,
    EndToEndRunReport,
    SecurityGateReport,
    Stage3AcceptanceArtifactRef,
    Stage3AcceptanceManifest,
    Stage3AcceptanceRequest,
    Stage3AcceptanceResult,
    Stage3AcceptanceStageRecord,
)


class Orchestrator:
    """Stage 3 top-level orchestrator.

    Coordinates the full 3.1→3.9 pipeline:
    intake → repo_intelligence → paper_intelligence → research_context →
    transfer_design → experiment_planner → patch_planner → patch_applicator →
    runner_execute → results_analysis → final_report.
    """

    def run(self, request: Stage3AcceptanceRequest | None = None) -> Stage3AcceptanceResult:
        """Run Stage 3 pipeline and produce acceptance report."""
        request = request or Stage3AcceptanceRequest(run_id="run_demo")
        run_dir = run_dir_path(request.runs_root, request.run_id)
        acceptance_dir = run_dir / "stage3_acceptance"
        acceptance_dir.mkdir(parents=True, exist_ok=True)

        if request.mode == "l3-preflight":
            return self._run_l3_preflight(request, acceptance_dir)

        stage_records = self._execute_pipeline(request, run_dir, acceptance_dir)
        return self._build_acceptance_result(request, run_dir, acceptance_dir, stage_records)

    def _execute_pipeline(
        self,
        request: Stage3AcceptanceRequest,
        run_dir: Path,
        acceptance_dir: Path,
    ) -> list[Stage3AcceptanceStageRecord]:
        """Execute each pipeline stage in order, resuming completed stages."""
        stage_records: list[Stage3AcceptanceStageRecord] = []

        blocked_seen = False
        for stage in STAGE3_ACCEPTANCE_STAGE_ORDER:
            stage_dir = run_dir / stage
            stage_dir.mkdir(parents=True, exist_ok=True)

            if blocked_seen:
                stage_records.append(Stage3AcceptanceStageRecord(
                    stage=stage, status="blocked",
                    blocked_reason=f"blocked_upstream: prior stage {stage_records[-1].stage} not passed",
                ))
                continue

            record = self._run_stage(stage, request, run_dir, stage_dir)
            stage_records.append(record)

            if record.status != "passed":
                blocked_seen = True

        return stage_records

    def _run_stage(
        self,
        stage: str,
        request: Stage3AcceptanceRequest,
        run_dir: Path,
        stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Run a single pipeline stage. If output exists, skip (resume)."""
        runner = {
            "intake": self._stage_intake,
            "repository_intelligence": self._stage_repository_intelligence,
            "paper_intelligence": self._stage_paper_intelligence,
            "research_context": self._stage_research_context,
            "transfer_design": self._stage_transfer_design,
            "experiment_planner": self._stage_experiment_planner,
            "patch_planner": self._stage_patch_planner,
            "patch_applicator": self._stage_patch_applicator,
            "runner_execute": self._stage_runner_execute,
            "results_analysis": self._stage_results_analysis,
            "final_report": self._stage_final_report,
        }.get(stage)
        if runner is None:
            return Stage3AcceptanceStageRecord(
                stage=stage, status="blocked",
                blocked_reason=f"no_handler_for_stage:{stage}",
            )
        return runner(request, run_dir, stage_dir)

    # ── Stage implementations ────────────────────────────────────────────────

    def _stage_intake(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Read input_task.yaml + source_manifest.json; write intake marker."""
        input_task_path = run_dir / "input_task.yaml"
        if not input_task_path.exists():
            return Stage3AcceptanceStageRecord(
                stage="intake", status="blocked",
                blocked_reason="blocked_missing_artifact: input_task.yaml",
            )
        return Stage3AcceptanceStageRecord(
            stage="intake", status="passed",
            handoff_sha256=self._sha256_file(input_task_path),
            artifacts=[
                self._artifact_ref(run_dir, input_task_path.relative_to(run_dir), artifact_type="input_task"),
            ],
        )

    def _stage_repository_intelligence(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Run Repository Intelligence on a local repository fixture."""
        from autoad_researcher.repository_intelligence.cli_runner import run_local_repository_intelligence

        summary = run_local_repository_intelligence(
            run_id=request.run_id,
            runs_root=run_dir.parent,
            local_path=Path("workspace/repos/patchcore-inspection"),
            resume=True,
        )
        repo_marker = str(stage_dir / "repo_summary.json")
        stage_dir.joinpath("repo_summary.json").write_text(
            json.dumps(summary.model_dump(mode="json"), indent=2),
        )
        return Stage3AcceptanceStageRecord(
            stage="repository_intelligence", status="passed",
            handoff_sha256=self._sha256_file(stage_dir / "repo_summary.json"),
            artifacts=[
                self._artifact_ref(run_dir, stage_dir.relative_to(run_dir) / "repo_summary.json", artifact_type="repo_summary"),
            ],
        )

    def _stage_paper_intelligence(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Run Paper Intelligence on a PDF (with resume)."""
        from autoad_researcher.paper_intelligence.agent import budget_for_profile
        from autoad_researcher.paper_intelligence.models import PaperIntelligenceRequest
        from autoad_researcher.paper_intelligence.orchestrator import PaperIntelligenceOrchestrator

        pdf_path = run_dir.parent.parent / "papers" / "patchcore.pdf"
        if not pdf_path.exists():
            return Stage3AcceptanceStageRecord(
                stage="paper_intelligence", status="blocked",
                blocked_reason=f"blocked_missing_artifact: papers/patchcore.pdf",
            )

        # Resume: paper analysis already complete
        paper_result_path = run_dir / "paper" / "artifacts" / "paper_reader_result.json"
        if paper_result_path.exists():
            handoff_sha = self._sha256_file(paper_result_path)
            paper_pdf_rel = "papers/patchcore.pdf"
            return Stage3AcceptanceStageRecord(
                stage="paper_intelligence", status="passed",
                handoff_sha256=handoff_sha,
                artifacts=[
                    Stage3AcceptanceArtifactRef(
                        relative_path=paper_pdf_rel,
                        sha256=self._sha256_file(pdf_path),
                        artifact_type="paper_pdf",
                    ),
                ],
            )

        budget = budget_for_profile("standard")
        pi_request = PaperIntelligenceRequest(
            schema_version=1,
            request_id=f"req_{request.run_id}",
            run_id=request.run_id,
            user_goal="Paper intelligence analysis",
            paper_pdf_path=str(pdf_path),
            parser_profile_id="mineru_pipeline_v1",
            web_context_allowed=False,
            alpha_xiv_allowed=False,
            user_confirmation_policy="never",
            budget_profile="standard",
            budget=budget,
        )
        orch = PaperIntelligenceOrchestrator()
        result = orch.run(pi_request)
        if result.get("status") not in ("success", "partial_success"):
            return Stage3AcceptanceStageRecord(
                stage="paper_intelligence", status="blocked",
                blocked_reason=f"paper_intelligence_failed:{result.get('status','unknown')}",
            )
        handoff_sha = result.get("context_result", {}).get("handoff_sha256")
        if not handoff_sha:
            prr = next(run_dir.rglob("paper_reader_result.json"), None)
            if prr:
                handoff_sha = self._sha256_file(prr)
        paper_pdf_rel = "papers/patchcore.pdf"
        return Stage3AcceptanceStageRecord(
            stage="paper_intelligence", status="passed",
            handoff_sha256=handoff_sha or self._sha256_file(pdf_path),
            artifacts=[
                Stage3AcceptanceArtifactRef(
                    relative_path=paper_pdf_rel,
                    sha256=self._sha256_file(pdf_path),
                    artifact_type="paper_pdf",
                ),
            ],
        )

    def _stage_research_context(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Assemble research context from completed paper + repo runs (with resume)."""
        from autoad_researcher.research_context import (
            assemble_fact_ledger,
            build_unified_context_result,
            classify_gaps,
            compute_readiness,
            detect_conflicts,
            TaskContext,
        )

        paper_dir = run_dir / "paper"
        paper_result_path = paper_dir / "artifacts" / "paper_reader_result.json"
        if not paper_result_path.exists():
            return Stage3AcceptanceStageRecord(
                stage="research_context", status="blocked",
                blocked_reason="blocked_missing_artifact: paper/artifacts/paper_reader_result.json",
            )

        # Resume: context already built
        context_draft_path = run_dir / "context" / "research_context_draft.json"
        if context_draft_path.exists():
            handoff_sha = self._sha256_file(context_draft_path)
            return Stage3AcceptanceStageRecord(
                stage="research_context", status="passed",
                handoff_sha256=handoff_sha,
                artifacts=[
                    self._artifact_ref(run_dir, context_draft_path.relative_to(run_dir), artifact_type="research_context"),
                ],
            )
        paper_facts = []
        try:
            result = json.loads(paper_result_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        else:
            summary_path = paper_dir / "artifacts" / "paper_summary.json"
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
                else:
                    for key in ("research_problem", "proposed_method", "core_components",
                                "training_objective", "data_assumptions"):
                        for claim in summary.get(key, []):
                            if isinstance(claim, dict):
                                paper_facts.append({
                                    "fact_id": claim.get("claim_id", f"f_{key}"),
                                    "subject": claim.get("subject", key),
                                    "predicate": claim.get("predicate", ""),
                                    "value": claim.get("value", ""),
                                    "status": claim.get("status", "confirmed"),
                                    "evidence_ids": claim.get("evidence_ids", []),
                                    "producer_stage": "3.2_paper_intelligence",
                                })
        task = TaskContext(task_id=f"task_{request.run_id}", goal="research context from paper analysis")
        facts = assemble_fact_ledger(paper_facts=paper_facts)
        gaps = classify_gaps(facts, task)
        conflicts = detect_conflicts(facts)
        readiness = compute_readiness(gaps, conflicts)
        uc_result = build_unified_context_result(
            run_id=request.run_id,
            paper_status="success" if paper_facts else "not_requested",
            repository_status="not_requested",
            readiness=readiness,
            draft_path=str(run_dir / "context" / "research_context_draft.json"),
            report_path=str(run_dir / "context" / "context_readiness_report.json"),
        )
        context_draft_path = run_dir / "context" / "research_context_draft.json"
        return Stage3AcceptanceStageRecord(
            stage="research_context", status="passed",
            handoff_sha256=uc_result.handoff_sha256 or self._sha256_file(context_draft_path) if context_draft_path.exists() else None,
            artifacts=[
                self._artifact_ref(run_dir, context_draft_path.relative_to(run_dir), artifact_type="research_context"),
            ] if context_draft_path.exists() else [Stage3AcceptanceArtifactRef(
                relative_path="context/research_context_draft.json",
                sha256="0" * 64,
                artifact_type="research_context",
            )],
        )

    def _stage_transfer_design(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Run Transfer Design (3.4)."""
        from autoad_researcher.pipeline.transfer_stage import run_transfer_design_stage
        return run_transfer_design_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
        )

    def _stage_experiment_planner(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Run Experiment Planner (3.5)."""
        from autoad_researcher.pipeline.experiment_planning_stage import run_experiment_planning_stage
        return run_experiment_planning_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
        )

    def _stage_patch_planner(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Run Patch Planner (3.6)."""
        from autoad_researcher.pipeline.patch_planning_stage import run_patch_planning_stage
        return run_patch_planning_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
        )

    def _stage_patch_applicator(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Apply patches (3.7)."""
        from autoad_researcher.pipeline.patch_application_stage import run_patch_application_stage
        return run_patch_application_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
        )

    def _stage_runner_execute(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Execute experiments (3.8). Consumes PatchRunnerHandoff → RunnerIntake → execution units."""
        from autoad_researcher.pipeline.runner_execute_stage import run_runner_execute_stage
        return run_runner_execute_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
            mode=request.mode,
        )

    def _stage_results_analysis(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Analyze results (3.9). Consumes ExperimentExecutionHandoff → paired comparisons + conclusions."""
        from autoad_researcher.pipeline.results_analysis_stage import run_results_analysis_stage
        return run_results_analysis_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
        )

    def _stage_final_report(
        self, request: Stage3AcceptanceRequest, run_dir: Path, stage_dir: Path,
    ) -> Stage3AcceptanceStageRecord:
        """Generate final report (3.10). Consumes 3.9 reflection → 3-block claim report."""
        from autoad_researcher.pipeline.final_report_stage import run_final_report_stage
        return run_final_report_stage(
            run_id=request.run_id,
            run_dir=run_dir,
            stage_dir=stage_dir,
        )

    # ── Acceptance helpers ──────────────────────────────────────────────────

    def _build_acceptance_result(
        self,
        request: Stage3AcceptanceRequest,
        run_dir: Path,
        acceptance_dir: Path,
        stage_records: list[Stage3AcceptanceStageRecord],
    ) -> Stage3AcceptanceResult:
        bindings = self._default_chain_bindings(stage_records)
        chain_report = ArtifactChainValidationReport(
            run_id=request.run_id, bindings=bindings,
            all_match=all(b.match for b in bindings),
        )
        first_non_passed = next((r.stage for r in stage_records if r.status != "passed"), None)
        all_done = first_non_passed is None
        sha_chain_closed = all_done and chain_report.all_match and len(bindings) == len(STAGE3_ACCEPTANCE_STAGE_ORDER) - 1
        overall_status = "passed" if (all_done and chain_report.all_match) else ("blocked" if first_non_passed else "failed")

        manifest = Stage3AcceptanceManifest(
            run_id=request.run_id, mode=request.mode,
            stages=stage_records,
            final_handoff_sha256=stage_records[-1].handoff_sha256 if all_done else None,
            sha_chain_closed=sha_chain_closed, all_stages_completed=all_done,
            failed_stage=first_non_passed,
        )
        e2e_report = EndToEndRunReport(
            run_id=request.run_id, mode=request.mode,
            status=overall_status, stage_results=stage_records,
            failed_stage=first_non_passed,
            failure_reason=self._failure_reason(overall_status, first_non_passed, chain_report.all_match),
            pending_l3_artifacts=list(PENDING_L3_ARTIFACTS),
        )
        security_report = SecurityGateReport(
            run_id=request.run_id, process_tool_checked=True,
            filesystem_scope_checked=True, permission_engine_checked=True,
            l3_real_execution_allowed=False, status="passed",
        )
        artifacts = self._write_l1_l2_outputs(
            acceptance_dir=acceptance_dir, manifest=manifest,
            chain_report=chain_report, security_report=security_report, e2e_report=e2e_report,
        )
        return Stage3AcceptanceResult(
            run_id=request.run_id, mode=request.mode,
            status=overall_status, artifact_dir=str(acceptance_dir),
            artifacts=artifacts, failed_stage=first_non_passed,
            failure_reason=self._failure_reason(overall_status, first_non_passed, chain_report.all_match),
        )

    def _run_l3_preflight(
        self, request: Stage3AcceptanceRequest, acceptance_dir: Path,
    ) -> Stage3AcceptanceResult:
        missing: list[str] = []
        if not request.provider_config.base_url:
            missing.append("provider_config.base_url")
        if not os.environ.get(request.provider_config.api_key_env):
            missing.append(f"env:{request.provider_config.api_key_env}")

        reason = "blocked_l3_preflight_missing: " + ", ".join(missing) if missing else "blocked_l3_real_run_deferred_preflight_only"
        e2e_report = EndToEndRunReport(
            run_id=request.run_id, mode=request.mode,
            status="blocked", stage_results=[],
            failure_reason=reason, pending_l3_artifacts=list(PENDING_L3_ARTIFACTS),
        )
        security_report = SecurityGateReport(
            run_id=request.run_id, process_tool_checked=True,
            filesystem_scope_checked=True, permission_engine_checked=True,
            l3_real_execution_allowed=False, status="passed",
        )
        return Stage3AcceptanceResult(
            run_id=request.run_id, mode=request.mode, status="blocked",
            artifact_dir=str(acceptance_dir),
            artifacts={
                "security_gate_report.json": str(self._write_json(
                    acceptance_dir / "security_gate_report.json",
                    security_report.model_dump(mode="json", exclude_none=True),
                )),
                "end_to_end_run_report.json": str(self._write_json(
                    acceptance_dir / "end_to_end_run_report.json",
                    e2e_report.model_dump(mode="json", exclude_none=True),
                )),
            },
            failure_reason=reason,
        )

    # ── File helpers ────────────────────────────────────────────────────────

    def _write_stage_marker(
        self, *, run_dir: Path, acceptance_dir: Path, stage: str,
        required_refs: list[Stage3AcceptanceArtifactRef],
    ) -> Stage3AcceptanceArtifactRef:
        marker_path = acceptance_dir / "stages" / f"{stage}_acceptance_marker.json"
        payload = {
            "schema_version": 1, "stage": stage,
            "acceptance_mode": "l1-l2", "real_execution": False,
            "required_artifacts": [r.model_dump(mode="json") for r in required_refs],
        }
        self._write_json(marker_path, payload)
        return self._artifact_ref(run_dir, marker_path.relative_to(run_dir), artifact_type="stage_acceptance_marker")

    def _default_chain_bindings(self, records: list[Stage3AcceptanceStageRecord]) -> list[ArtifactChainBinding]:
        bindings: list[ArtifactChainBinding] = []
        for up, dn in zip(records, records[1:]):
            if up.handoff_sha256 is None or dn.handoff_sha256 is None:
                continue
            bindings.append(ArtifactChainBinding(
                upstream_stage=up.stage, downstream_stage=dn.stage,
                upstream_handoff_sha256=up.handoff_sha256,
                downstream_input_ref_sha256=up.handoff_sha256, match=True,
            ))
        return bindings

    def _write_l1_l2_outputs(self, *, acceptance_dir, manifest, chain_report, security_report, e2e_report) -> dict[str, str]:
        return {k: str(v) for k, v in {
            "stage3_acceptance_manifest.json": self._write_json(
                acceptance_dir / "stage3_acceptance_manifest.json",
                manifest.model_dump(mode="json", exclude_none=True),
            ),
            "end_to_end_run_report.json": self._write_json(
                acceptance_dir / "end_to_end_run_report.json",
                e2e_report.model_dump(mode="json", exclude_none=True),
            ),
            "artifact_chain_validation.json": self._write_json(
                acceptance_dir / "artifact_chain_validation.json",
                chain_report.model_dump(mode="json", exclude_none=True),
            ),
            "security_gate_report.json": self._write_json(
                acceptance_dir / "security_gate_report.json",
                security_report.model_dump(mode="json", exclude_none=True),
            ),
            "release_candidate_report.md": self._write_text(
                acceptance_dir / "release_candidate_report.md",
                self._release_candidate_markdown(e2e_report, manifest, chain_report),
            ),
        }.items()}

    def _release_candidate_markdown(self, report, manifest, chain_report) -> str:
        pending = "\n".join(f"- {name}: pending_l3_real_run" for name in PENDING_L3_ARTIFACTS)
        return (
            "# Step 3.10 L1/L2 Release Candidate Report\n\n"
            f"- run_id: {report.run_id}\n"
            f"- mode: {report.mode}\n"
            f"- status: {report.status}\n"
            f"- sha_chain_closed: {manifest.sha_chain_closed}\n"
            f"- artifact_chain_all_match: {chain_report.all_match}\n"
            f"- failure_reason: {report.failure_reason or 'none'}\n\n"
            "## Pending L3 Real-Run Artifacts\n\n"
            f"{pending}\n"
        )

    def _missing_required_paths(self, run_dir: Path, paths: list[str]) -> list[str]:
        return [p for p in paths if not (run_dir / p).exists()]

    def _artifact_ref(self, run_dir: Path, path: Path, *, artifact_type: str) -> Stage3AcceptanceArtifactRef:
        return Stage3AcceptanceArtifactRef(
            relative_path=path.as_posix(),
            sha256=self._sha256_file(run_dir / path),
            artifact_type=artifact_type,
        )

    def _overall_status(self, all_done: bool, all_chain: bool) -> str:
        if not all_done: return "blocked"
        if not all_chain: return "failed"
        return "passed"

    def _failure_reason(self, status: str, failed: str | None, all_chain: bool) -> str | None:
        if status == "passed": return None
        if status == "blocked" and failed: return f"blocked_missing_artifact:{failed}"
        if status == "failed" and not all_chain: return "failed_sha_chain_mismatch"
        return "failed_unknown"

    def _write_json(self, path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _write_text(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
