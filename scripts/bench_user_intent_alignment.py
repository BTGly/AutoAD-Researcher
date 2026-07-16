#!/usr/bin/env python3
"""Collect raw observations for the P0 user-intent-alignment cases.

Does NOT score — writes observations.jsonl for manual judgment.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
    save_research_intent_summary,
)
from autoad_researcher.worker.main import _process_pending_jobs

DEFAULT_RUBRIC = Path("configs/benchmarks/user_intent_p0_cases_v1.json")
BASE_RUNS = Path("runs")
MATERIAL_JOB_TYPES = {
    "git_clone",
    "repo_summarize",
    "repo_analyze",
    "web_fetch",
    "web_markitdown",
    "paper_download",
    "paper_parse",
    "paper_parse_mineru",
    "paper_parse_markitdown",
    "paper_summarize",
    "document_markitdown",
}


class SourceFixture(BaseModel):
    """A registered source and, optionally, its immutable parse evidence."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    kind: Literal["paper_pdf", "github_repo"]
    user_label: str = Field(min_length=1)
    status: str = Field(min_length=1)
    stored_file: str | None = None
    parse_attempt_id: str | None = None
    parse_status: Literal["ok", "failed"] | None = None
    title: str = ""
    proposed_method: str = ""
    research_problem: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.parse_status is not None and self.kind != "paper_pdf":
            raise ValueError("parse status is only valid for paper_pdf sources")
        if self.parse_status is not None and not self.parse_attempt_id:
            raise ValueError("parse_attempt_id is required when parse_status is set")
        if self.parse_status == "ok" and not self.title.strip():
            raise ValueError("parsed paper fixtures require a title")


class SourceRegistryFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_type: Literal["source_registry"]
    sources: list[SourceFixture] = Field(min_length=1)


class IntentSummaryFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_type: Literal["intent_summary"]
    goal: str = Field(min_length=1)
    confirmed_facts: list[str] = Field(default_factory=list)
    inferred_facts: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    blocking_question: str | None = None


class IndependentRunFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_type: Literal["independent_run"]
    run_name: str = Field(min_length=1)
    fixtures: list["Fixture"] = Field(min_length=1)


Fixture = Annotated[
    SourceRegistryFixture | IntentSummaryFixture | IndependentRunFixture,
    Field(discriminator="fixture_type"),
]
IndependentRunFixture.model_rebuild()


class ExpectedControlAxes(BaseModel):
    """Machine-readable expectations for the orthogonal V2 control plane."""

    model_config = ConfigDict(extra="forbid")

    dialogue_mode: Literal["ask", "plan", "act"] | None = None
    action_scope: Literal[
        "none", "source", "repository", "code", "experiment", "system"
    ] | None = None
    policy: Literal["allow", "ask_permission", "deny"] | None = None
    evidence_status: Literal[
        "sufficient", "insufficient", "conflicting", "unavailable"
    ] | None = None
    conversation_transition: Literal[
        "new", "continue", "revise", "confirm", "cancel"
    ] | None = None
    feasibility: Literal[
        "not_assessed", "feasible", "infeasible_as_stated"
    ] | None = None
    numeric_claim_allowed: bool | None = None
    source_permission: Literal["allow", "ask", "deny"] | None = None


class HardConstraints(BaseModel):
    """Deterministic side-effect invariants, separate from LLM semantic review."""

    model_config = ConfigDict(extra="forbid")

    no_execution_side_effects: bool = True
    deny_must_not_dispatch: bool = False
    preserve_existing_parse_attempts: bool = False
    no_duplicate_pending_source_job: bool = False


class IntentCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    category: str
    expected_mode: str
    source_url: str = ""
    turns: list[str]
    expected: dict[str, Any]
    paraphrases: list[str] = Field(default_factory=list)
    expected_control: ExpectedControlAxes = Field(default_factory=ExpectedControlAxes)
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    run_topology: Literal["single_run", "isolated_runs"] = "single_run"
    fixtures: list[Fixture] = Field(default_factory=list)
    setup_note: str | None = Field(default=None, alias="_setup_note")


class IntentCaseCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    description: str = ""
    cases: list[IntentCase]


class IntentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = ""
    category: str = ""
    expected_mode: str = ""
    turn_index: int | None = None
    variant: str = "original"
    user_input: str
    assistant_reply: str
    summary: dict[str, Any] | None = None
    source_action: dict[str, str] | None = None
    source_permission: dict[str, Any] | None = None
    experiment_task: dict[str, Any] | None = None
    dialogue_mode: str = ""
    action_scope: str = "none"
    policy: str = "allow"
    evidence_status: str = "unavailable"
    conversation_transition: str = "new"
    feasibility: str = "not_assessed"
    numeric_claim_allowed: bool = True
    policy_assessment: dict[str, str] = Field(default_factory=dict)
    source_state_before: list[dict[str, Any]] = Field(default_factory=list)
    source_state: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_jobs: list[dict[str, Any]] = Field(default_factory=list)
    created_sources: list[dict[str, Any]] = Field(default_factory=list)
    created_jobs: list[dict[str, Any]] = Field(default_factory=list)
    experiment_session_created: bool = False
    code_modified: bool = False
    boundary_violations: list[str] = Field(default_factory=list)
    control_mismatches: list[str] = Field(default_factory=list)
    hard_failures: list[str] = Field(default_factory=list)
    error: str = ""


def load_corpus(path: Path) -> IntentCaseCorpus:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return IntentCaseCorpus.model_validate(raw)


def _run_dialogue(
    run_dir: Path,
    user_input: str,
    transcript: list[dict[str, str]],
    api_key: str,
    provider_url: str,
    model: str,
) -> IntentObservation:
    source_state_before = _source_state_snapshot(run_dir)
    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=user_input,
        transcript_tail=transcript[-12:],
        api_key=api_key,
        provider_url=provider_url,
        model=model,
    )
    transcript.append({"role": "user", "content": user_input})
    transcript.append({"role": "assistant", "content": result.reply})

    summary = load_research_intent_summary(run_dir)
    jobs = load_pipeline_jobs(run_dir)
    non_material = [j for j in jobs if j.get("job_type") not in MATERIAL_JOB_TYPES]

    boundary_violations = []
    if non_material:
        boundary_violations.append("experiment_job_created")
    if (run_dir / "experiments" / "sessions").exists():
        boundary_violations.append("experiment_session_created")
    if any((run_dir / rel).exists() for rel in ("code", "patches", "workspace/code")):
        boundary_violations.append("code_modified")

    return IntentObservation(
        user_input=user_input,
        assistant_reply=result.reply,
        summary=summary.model_dump(mode="json") if summary else None,
        source_action=result.source_action,
        source_permission=result.source_permission,
        experiment_task=result.experiment_task,
        dialogue_mode=result.dialogue_mode,
        action_scope=result.action_scope,
        policy=result.policy,
        evidence_status=result.evidence_status,
        conversation_transition=result.conversation_transition,
        feasibility=result.feasibility,
        numeric_claim_allowed=result.numeric_claim_allowed,
        policy_assessment=result.policy_assessment,
        source_state_before=source_state_before,
        source_state=_source_state_snapshot(run_dir),
        pipeline_jobs=jobs,
        created_sources=result.created_sources,
        created_jobs=result.created_jobs,
        experiment_session_created=(run_dir / "experiments" / "sessions").exists(),
        code_modified=any(
            (run_dir / rel).exists()
            for rel in ("code", "patches", "workspace/code")
        ),
        boundary_violations=boundary_violations,
    )


def _source_state_snapshot(run_dir: Path) -> list[dict[str, Any]]:
    registry_path = run_dir / "sources" / "source_references.json"
    if not registry_path.is_file():
        return []
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    sources = registry.get("sources", [])
    return [source for source in sources if isinstance(source, dict)]


def _apply_source_registry_fixture(
    run_dir: Path,
    fixture: SourceRegistryFixture,
) -> None:
    registry_sources: list[dict[str, Any]] = []
    for source in fixture.sources:
        stored_path = ""
        if source.stored_file is not None:
            stored_path = f"sources/{source.source_id}/{source.stored_file}"
            file_path = run_dir / stored_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b"%PDF-1.4\n")

        source_record: dict[str, Any] = {
            "source_id": source.source_id,
            "kind": source.kind,
            "user_label": source.user_label,
            "stored_path": stored_path,
            "status": source.status,
        }
        if source.parse_status is not None:
            attempt_id = source.parse_attempt_id
            assert attempt_id is not None
            quality_report = f"paper/parse/attempts/{attempt_id}/parse_quality_report.json"
            source_record["parse_attempts"] = [{
                "parse_attempt_id": attempt_id,
                "status": source.parse_status,
                "parser": "mineru_pipeline_v1",
                "quality_report": quality_report,
            }]
            source_record["active_parse_attempt_id"] = (
                attempt_id if source.parse_status == "ok" else None
            )
            if source.parse_status == "ok":
                paper_dir = run_dir / "paper" / "parse" / "attempts" / attempt_id
                paper_dir.mkdir(parents=True, exist_ok=True)
                (paper_dir / "paper_summary.json").write_text(
                    json.dumps({
                        "title": source.title,
                        "proposed_method": source.proposed_method,
                        "research_problem": source.research_problem,
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                (paper_dir / "parse_quality_report.json").write_text(
                    json.dumps({
                        "quality_level": "usable",
                        "source_id": source.source_id,
                        "parser": "mineru_pipeline_v1",
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
        registry_sources.append(source_record)
    registry_path = run_dir / "sources" / "source_references.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"sources": registry_sources}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _apply_fixture(run_dir: Path, fixture: Fixture) -> None:
    if isinstance(fixture, SourceRegistryFixture):
        _apply_source_registry_fixture(run_dir, fixture)
    elif isinstance(fixture, IntentSummaryFixture):
        save_research_intent_summary(
            run_dir,
            ResearchIntentSummary(
                goal=fixture.goal,
                confirmed_facts=fixture.confirmed_facts,
                inferred_facts=fixture.inferred_facts,
                unresolved_conflicts=fixture.unresolved_conflicts,
                blocking_question=fixture.blocking_question,
            ),
        )
    else:
        raise ValueError("independent_run fixtures must be applied at the suite level")


def _prepare_case_runs(case: IntentCase, suite_dir: Path) -> Path:
    primary_run_dir = suite_dir / case.case_id
    primary_run_dir.mkdir(parents=True, exist_ok=False)
    for fixture in case.fixtures:
        if isinstance(fixture, IndependentRunFixture):
            if case.run_topology != "isolated_runs":
                raise ValueError("independent_run requires run_topology=isolated_runs")
            fixture_run_dir = suite_dir / f"{case.case_id}__{fixture.run_name}"
            fixture_run_dir.mkdir(parents=True, exist_ok=False)
            for nested_fixture in fixture.fixtures:
                if isinstance(nested_fixture, IndependentRunFixture):
                    raise ValueError("independent_run fixtures cannot be nested")
                _apply_fixture(fixture_run_dir, nested_fixture)
            continue
        _apply_fixture(primary_run_dir, fixture)
    return primary_run_dir


def _process_until_idle(run_dir: Path, limit: int = 10) -> None:
    for _ in range(limit):
        if _process_pending_jobs(run_dir) == 0:
            return
    raise RuntimeError(f"material jobs did not become idle for {run_dir.name}")


def run_case(
    case: IntentCase,
    *,
    run_dir: Path,
    api_key: str,
    provider_url: str,
    model: str,
    all_observations: list[IntentObservation],
) -> None:
    transcript: list[dict[str, str]] = []
    source_url = case.source_url.strip()

    if source_url and (
        source_url.startswith("http://") or source_url.startswith("https://")
    ):
        obs = _run_dialogue(
            run_dir, source_url, transcript, api_key, provider_url, model
        )
        all_observations.append(_annotate_observation(
            obs, case, turn_index=-1, variant="source_registration"
        ))
        _process_until_idle(run_dir)

    for idx, turn in enumerate(case.turns):
        obs = _run_dialogue(
            run_dir, turn, transcript, api_key, provider_url, model
        )
        all_observations.append(_annotate_observation(
            obs, case, turn_index=idx, variant="original"
        ))

    (run_dir / "_observations.json").write_text(
        json.dumps(
            [o.model_dump(mode="json") for o in all_observations
             if o.case_id == case.case_id],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _annotate_observation(
    observation: IntentObservation,
    case: IntentCase,
    *,
    turn_index: int,
    variant: str,
) -> IntentObservation:
    observation.case_id = case.case_id
    observation.category = case.category
    observation.expected_mode = case.expected_mode
    observation.turn_index = turn_index
    observation.variant = variant
    observation.control_mismatches = _control_mismatches(case, observation)
    observation.hard_failures = _evaluate_hard_constraints(case, observation)
    return observation


def _control_mismatches(
    case: IntentCase,
    observation: IntentObservation,
) -> list[str]:
    expected = case.expected_control
    actual = {
        "dialogue_mode": observation.dialogue_mode,
        "action_scope": observation.action_scope,
        "policy": observation.policy,
        "evidence_status": observation.evidence_status,
        "conversation_transition": observation.conversation_transition,
        "feasibility": observation.feasibility,
        "numeric_claim_allowed": observation.numeric_claim_allowed,
        "source_permission": (
            observation.source_permission.get("permission_decision")
            if observation.source_permission is not None
            else None
        ),
    }
    return [
        field_name
        for field_name, expected_value in expected.model_dump().items()
        if expected_value is not None and actual[field_name] != expected_value
    ]


def _evaluate_hard_constraints(
    case: IntentCase,
    observation: IntentObservation,
) -> list[str]:
    failures: list[str] = []
    constraints = case.hard_constraints
    if constraints.no_execution_side_effects and observation.boundary_violations:
        failures.extend(observation.boundary_violations)
    if constraints.deny_must_not_dispatch and observation.policy == "deny":
        if observation.source_action or observation.created_jobs or observation.experiment_task:
            failures.append("policy_denied_action_dispatched")
    if constraints.preserve_existing_parse_attempts:
        before_attempts = _parse_attempts_by_source(observation.source_state_before)
        after_attempts = _parse_attempts_by_source(observation.source_state)
        for source_id, attempts in before_attempts.items():
            if not attempts.issubset(after_attempts.get(source_id, set())):
                failures.append("existing_parse_attempt_overwritten")
                break
    if constraints.no_duplicate_pending_source_job:
        pending_identities: set[tuple[str, str, str]] = set()
        for job in observation.pipeline_jobs:
            if job.get("status") not in {"queued", "running"}:
                continue
            payload = job.get("payload")
            if not isinstance(payload, dict):
                continue
            requested_action = payload.get("requested_action")
            if not isinstance(requested_action, str):
                continue
            identity = (
                str(job.get("source_id") or ""),
                str(job.get("job_type") or ""),
                requested_action,
            )
            if identity in pending_identities:
                failures.append("duplicate_pending_source_job")
                break
            pending_identities.add(identity)
    return failures


def _parse_attempts_by_source(
    sources: list[dict[str, Any]],
) -> dict[str, set[str]]:
    parsed: dict[str, set[str]] = {}
    for source in sources:
        source_id = source.get("source_id")
        attempts = source.get("parse_attempts")
        if not isinstance(source_id, str) or not isinstance(attempts, list):
            continue
        parsed[source_id] = {
            str(attempt.get("parse_attempt_id"))
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("parse_attempt_id")
        }
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--runs-root", type=Path, default=BASE_RUNS)
    parser.add_argument(
        "--model",
        default=os.environ.get("AUTOAD_DIALOGUE_MODEL", "deepseek-v4-flash"),
    )
    parser.add_argument("--cases", nargs="*", default=[],
                       help="Run only these case IDs. If empty, run all.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider_url = os.environ.get("DEEPSEEK_BASE_URL", "")
    if not api_key or not provider_url:
        raise SystemExit("DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL are required")

    corpus = load_corpus(args.config)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite_dir = args.runs_root / f"intent_alignment_{timestamp}"
    suite_dir.mkdir(parents=True, exist_ok=False)

    all_observations: list[IntentObservation] = []
    target_ids = set(args.cases) if args.cases else None

    for case in corpus.cases:
        if target_ids and case.case_id not in target_ids:
            continue

        print(f"\n[P0-intent] ==== {case.case_id} ({case.expected_mode}) ====", flush=True)
        try:
            case_run_dir = _prepare_case_runs(case, suite_dir)
            run_case(
                case,
                run_dir=case_run_dir,
                api_key=api_key,
                provider_url=provider_url,
                model=args.model,
                all_observations=all_observations,
            )
        except Exception as exc:
            print(f"[P0-intent] {case.case_id} ERROR: {exc}", flush=True)
            all_observations.append(IntentObservation(
                case_id=case.case_id,
                category=case.category,
                expected_mode=case.expected_mode,
                user_input="",
                assistant_reply="",
                error=str(exc),
            ))

    output_path = suite_dir / "observations.jsonl"
    with output_path.open("w", encoding="utf-8") as f:
        for obs in all_observations:
            f.write(json.dumps(obs.model_dump(mode="json"), ensure_ascii=False) + "\n")

    print(f"\n[P0-intent] {len(all_observations)} observations → {output_path}")
    print(f"[P0-intent] Run dirs: {suite_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
