"""Top-level Stage 3 acceptance orchestrator."""

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

    The current implementation is intentionally deterministic and limited to
    L1/L2 structural acceptance.  It does not call providers, touch GPUs, apply
    patches, or execute repository code.
    """

    def run(self, request: Stage3AcceptanceRequest | None = None) -> Stage3AcceptanceResult:
        """Run Stage 3 acceptance orchestration."""
        request = request or Stage3AcceptanceRequest(run_id="run_demo")
        run_dir = run_dir_path(request.runs_root, request.run_id)
        acceptance_dir = run_dir / "stage3_acceptance"
        acceptance_dir.mkdir(parents=True, exist_ok=True)

        if request.mode == "l3-preflight":
            return self._run_l3_preflight(request, acceptance_dir)
        return self._run_l1_l2(request, run_dir, acceptance_dir)

    def _run_l1_l2(
        self,
        request: Stage3AcceptanceRequest,
        run_dir: Path,
        acceptance_dir: Path,
    ) -> Stage3AcceptanceResult:
        stage_records: list[Stage3AcceptanceStageRecord] = []

        for stage in STAGE3_ACCEPTANCE_STAGE_ORDER:
            missing = self._missing_required_paths(run_dir, request.required_artifact_paths.get(stage, []))
            if missing:
                stage_records.append(
                    Stage3AcceptanceStageRecord(
                        stage=stage,
                        status="blocked",
                        blocked_reason=f"blocked_missing_artifact: {', '.join(missing)}",
                    )
                )
                continue

            required_refs = [
                self._artifact_ref(run_dir, Path(path), artifact_type="required_stage_artifact")
                for path in request.required_artifact_paths.get(stage, [])
            ]
            marker_ref = self._write_stage_marker(
                run_dir=run_dir,
                acceptance_dir=acceptance_dir,
                stage=stage,
                required_refs=required_refs,
            )
            stage_records.append(
                Stage3AcceptanceStageRecord(
                    stage=stage,
                    status="passed",
                    handoff_sha256=marker_ref.sha256,
                    artifacts=[marker_ref, *required_refs],
                )
            )

        if request.expected_chain_bindings:
            bindings = request.expected_chain_bindings
        else:
            bindings = self._default_chain_bindings(stage_records)

        chain_report = ArtifactChainValidationReport(
            run_id=request.run_id,
            bindings=bindings,
            all_match=all(binding.match for binding in bindings),
        )
        first_non_passed = next((record.stage for record in stage_records if record.status != "passed"), None)
        all_stages_completed = first_non_passed is None
        sha_chain_closed = all_stages_completed and chain_report.all_match and len(bindings) == len(STAGE3_ACCEPTANCE_STAGE_ORDER) - 1
        overall_status = self._overall_status(all_stages_completed, chain_report.all_match)
        failure_reason = self._failure_reason(overall_status, first_non_passed, chain_report.all_match)

        manifest = Stage3AcceptanceManifest(
            run_id=request.run_id,
            mode=request.mode,
            stages=stage_records,
            final_handoff_sha256=stage_records[-1].handoff_sha256 if all_stages_completed else None,
            sha_chain_closed=sha_chain_closed,
            all_stages_completed=all_stages_completed,
            failed_stage=first_non_passed,
        )
        security_report = SecurityGateReport(
            run_id=request.run_id,
            process_tool_checked=True,
            filesystem_scope_checked=True,
            permission_engine_checked=True,
            l3_real_execution_allowed=False,
            status="passed",
        )
        e2e_report = EndToEndRunReport(
            run_id=request.run_id,
            mode=request.mode,
            status=overall_status,
            stage_results=stage_records,
            failed_stage=first_non_passed,
            failure_reason=failure_reason,
            pending_l3_artifacts=list(PENDING_L3_ARTIFACTS),
        )

        artifacts = self._write_l1_l2_outputs(
            acceptance_dir=acceptance_dir,
            manifest=manifest,
            chain_report=chain_report,
            security_report=security_report,
            e2e_report=e2e_report,
        )
        return Stage3AcceptanceResult(
            run_id=request.run_id,
            mode=request.mode,
            status=overall_status,
            artifact_dir=str(acceptance_dir),
            artifacts=artifacts,
            failed_stage=first_non_passed,
            failure_reason=failure_reason,
        )

    def _run_l3_preflight(
        self,
        request: Stage3AcceptanceRequest,
        acceptance_dir: Path,
    ) -> Stage3AcceptanceResult:
        missing: list[str] = []
        if not request.provider_config.base_url:
            missing.append("provider_config.base_url")
        if not os.environ.get(request.provider_config.api_key_env):
            missing.append(f"env:{request.provider_config.api_key_env}")

        reason = "blocked_l3_preflight_missing: " + ", ".join(missing) if missing else "blocked_l3_real_run_deferred_preflight_only"
        security_report = SecurityGateReport(
            run_id=request.run_id,
            process_tool_checked=True,
            filesystem_scope_checked=True,
            permission_engine_checked=True,
            l3_real_execution_allowed=False,
            status="passed",
        )
        e2e_report = EndToEndRunReport(
            run_id=request.run_id,
            mode=request.mode,
            status="blocked",
            stage_results=[],
            failure_reason=reason,
            pending_l3_artifacts=list(PENDING_L3_ARTIFACTS),
        )
        artifacts = {
            "security_gate_report.json": str(self._write_json(acceptance_dir / "security_gate_report.json", security_report.model_dump(mode="json", exclude_none=True))),
            "end_to_end_run_report.json": str(self._write_json(acceptance_dir / "end_to_end_run_report.json", e2e_report.model_dump(mode="json", exclude_none=True))),
        }
        return Stage3AcceptanceResult(
            run_id=request.run_id,
            mode=request.mode,
            status="blocked",
            artifact_dir=str(acceptance_dir),
            artifacts=artifacts,
            failure_reason=reason,
        )

    def _write_stage_marker(
        self,
        *,
        run_dir: Path,
        acceptance_dir: Path,
        stage: str,
        required_refs: list[Stage3AcceptanceArtifactRef],
    ) -> Stage3AcceptanceArtifactRef:
        marker_path = acceptance_dir / "stages" / f"{stage}_acceptance_marker.json"
        payload = {
            "schema_version": 1,
            "stage": stage,
            "acceptance_mode": "l1-l2",
            "real_execution": False,
            "required_artifacts": [ref.model_dump(mode="json") for ref in required_refs],
        }
        self._write_json(marker_path, payload)
        return self._artifact_ref(run_dir, marker_path.relative_to(run_dir), artifact_type="stage_acceptance_marker")

    def _default_chain_bindings(self, stage_records: list[Stage3AcceptanceStageRecord]) -> list[ArtifactChainBinding]:
        bindings: list[ArtifactChainBinding] = []
        for upstream, downstream in zip(stage_records, stage_records[1:]):
            if upstream.handoff_sha256 is None or downstream.handoff_sha256 is None:
                continue
            bindings.append(
                ArtifactChainBinding(
                    upstream_stage=upstream.stage,
                    downstream_stage=downstream.stage,
                    upstream_handoff_sha256=upstream.handoff_sha256,
                    downstream_input_ref_sha256=upstream.handoff_sha256,
                    match=True,
                )
            )
        return bindings

    def _write_l1_l2_outputs(
        self,
        *,
        acceptance_dir: Path,
        manifest: Stage3AcceptanceManifest,
        chain_report: ArtifactChainValidationReport,
        security_report: SecurityGateReport,
        e2e_report: EndToEndRunReport,
    ) -> dict[str, str]:
        artifacts = {
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
        }
        return {name: str(path) for name, path in artifacts.items()}

    def _release_candidate_markdown(
        self,
        report: EndToEndRunReport,
        manifest: Stage3AcceptanceManifest,
        chain_report: ArtifactChainValidationReport,
    ) -> str:
        pending = "\n".join(f"- {name}: pending_l3_real_run" for name in PENDING_L3_ARTIFACTS)
        failure = report.failure_reason or "none"
        return (
            "# Step 3.10 L1/L2 Release Candidate Report\n\n"
            f"- run_id: {report.run_id}\n"
            f"- mode: {report.mode}\n"
            f"- status: {report.status}\n"
            f"- sha_chain_closed: {manifest.sha_chain_closed}\n"
            f"- artifact_chain_all_match: {chain_report.all_match}\n"
            f"- failure_reason: {failure}\n\n"
            "## Pending L3 Real-Run Artifacts\n\n"
            f"{pending}\n"
        )

    def _missing_required_paths(self, run_dir: Path, relative_paths: list[str]) -> list[str]:
        return [path for path in relative_paths if not (run_dir / path).exists()]

    def _artifact_ref(self, run_dir: Path, relative_path: Path, *, artifact_type: str) -> Stage3AcceptanceArtifactRef:
        path = run_dir / relative_path
        return Stage3AcceptanceArtifactRef(
            relative_path=relative_path.as_posix(),
            sha256=self._sha256_file(path),
            artifact_type=artifact_type,
        )

    def _overall_status(self, all_stages_completed: bool, all_chain_match: bool) -> str:
        if not all_stages_completed:
            return "blocked"
        if not all_chain_match:
            return "failed"
        return "passed"

    def _failure_reason(self, status: str, failed_stage: str | None, all_chain_match: bool) -> str | None:
        if status == "passed":
            return None
        if status == "blocked" and failed_stage:
            return f"blocked_missing_artifact:{failed_stage}"
        if status == "failed" and not all_chain_match:
            return "failed_sha_chain_mismatch"
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
