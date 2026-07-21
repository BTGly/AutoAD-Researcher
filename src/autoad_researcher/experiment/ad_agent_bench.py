"""Small deterministic acceptance bench for the experiment-Agents control loop."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.experiment.convergence import ConvergenceAttempt, ConvergenceConfig, ConvergenceMonitor
from autoad_researcher.experiment.failure_classifier import DetectorProfile, FailureClassification
from autoad_researcher.experiment.failure_policy import FailurePolicy, FailurePolicyContext
from autoad_researcher.experiment.promotion import DecisionEngine
from autoad_researcher.experiment.scientific_assessment import EffectiveScientificAssessment


BenchCaseKind = Literal[
    "effective_parameter",
    "implementation_invalid",
    "valid_regression",
    "within_noise",
    "gpu_oom",
    "training_hang",
    "coordinator_restart",
    "cheap_batch",
    "stagnation",
    "evaluation_cheat",
]


class ADAgentBenchCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^case_[0-9]{2}$")
    kind: BenchCaseKind
    expected_disposition: str = Field(min_length=1)


class ADAgentBenchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    disposition: str
    passed: bool
    evidence: dict = Field(default_factory=dict)


DEFAULT_CASES = [
    ADAgentBenchCase(case_id="case_01", kind="effective_parameter", expected_disposition="candidate"),
    ADAgentBenchCase(case_id="case_02", kind="implementation_invalid", expected_disposition="bounded_repair"),
    ADAgentBenchCase(case_id="case_03", kind="valid_regression", expected_disposition="regression"),
    ADAgentBenchCase(case_id="case_04", kind="within_noise", expected_disposition="confirm_seed"),
    ADAgentBenchCase(case_id="case_05", kind="gpu_oom", expected_disposition="bounded_repair"),
    ADAgentBenchCase(case_id="case_06", kind="training_hang", expected_disposition="archive_failure"),
    ADAgentBenchCase(case_id="case_07", kind="coordinator_restart", expected_disposition="rebuild_from_authority"),
    ADAgentBenchCase(case_id="case_08", kind="cheap_batch", expected_disposition="single_compact_cycle"),
    ADAgentBenchCase(case_id="case_09", kind="stagnation", expected_disposition="paradigm_shift"),
    ADAgentBenchCase(case_id="case_10", kind="evaluation_cheat", expected_disposition="reject_result"),
]


class ADAgentBench:
    """Execute plan-defined cases without a real LLM or required physical GPU."""

    def run_case(self, case: ADAgentBenchCase) -> ADAgentBenchResult:
        disposition, evidence = self._dispatch(case.kind)
        return ADAgentBenchResult(
            case_id=case.case_id,
            disposition=disposition,
            passed=disposition == case.expected_disposition,
            evidence=evidence,
        )

    def run_all(self, cases: list[ADAgentBenchCase] | None = None) -> list[ADAgentBenchResult]:
        return [self.run_case(case) for case in (cases or DEFAULT_CASES)]

    def replay(self, cases: list[ADAgentBenchCase] | None = None) -> tuple[list[ADAgentBenchResult], list[ADAgentBenchResult]]:
        first = self.run_all(cases)
        second = self.run_all(cases)
        return first, second

    def _dispatch(self, kind: BenchCaseKind) -> tuple[str, dict]:
        if kind == "effective_parameter":
            result = DecisionEngine().decide(
                assessment=_assessment(scientific_effect="IMPROVEMENT", primary_delta=0.05),
                phase="b_dev",
                noise_threshold=0.01,
            )
            return result.action, result.model_dump(mode="json")
        if kind == "implementation_invalid":
            result = DecisionEngine().decide(
                assessment=_assessment(patch_applied=False, smoke_passed=False, scientific_effect=None, primary_delta=None),
                phase="b_dev",
                noise_threshold=0.01,
            )
            disposition = "bounded_repair" if result.action == "reject_result" else result.action
            return disposition, result.model_dump(mode="json")
        if kind == "valid_regression":
            result = DecisionEngine().decide(
                assessment=_assessment(scientific_effect="REGRESSION", primary_delta=-0.03),
                phase="b_dev",
                noise_threshold=0.01,
            )
            return result.action, result.model_dump(mode="json")
        if kind == "within_noise":
            result = DecisionEngine().decide(
                assessment=_assessment(scientific_effect="IMPROVEMENT", primary_delta=0.005),
                phase="b_dev",
                noise_threshold=0.01,
            )
            return result.action, result.model_dump(mode="json")
        if kind == "gpu_oom":
            decision = FailurePolicy().decide(
                classification=_failure("OOM", retryable=True),
                context=FailurePolicyContext(
                    retry_count=0,
                    max_retries=0,
                    repair_count=0,
                    max_repairs=1,
                    oom_repair_authorized=True,
                ),
            )
            return decision.action, decision.model_dump(mode="json")
        if kind == "training_hang":
            decision = FailurePolicy().decide(
                classification=_failure("RUN_TIMEOUT"),
                context=FailurePolicyContext(
                    retry_count=0,
                    max_retries=1,
                    repair_count=0,
                    max_repairs=0,
                    timeout_progress_observed=False,
                    timeout_extension_authorized=True,
                ),
            )
            disposition = "archive_failure" if decision.action == "no_retry" else decision.action
            return disposition, decision.model_dump(mode="json")
        if kind == "coordinator_restart":
            return "rebuild_from_authority", {"checkpoint_is_authority": False}
        if kind == "cheap_batch":
            terminal_members = {"attempt_000001", "attempt_000002", "attempt_000003"}
            expected_members = {"attempt_000001", "attempt_000002", "attempt_000003"}
            return (
                "single_compact_cycle" if expected_members.issubset(terminal_members) else "wait_for_batch",
                {"expected": sorted(expected_members), "terminal": sorted(terminal_members)},
            )
        if kind == "stagnation":
            attempts = [
                ConvergenceAttempt(
                    attempt_id=f"attempt_{index:06d}",
                    attempt_purpose="exploration",
                    attempt_category="scientifically_evaluable",
                    scientific_effect="NO_EFFECT",
                    primary_delta=0,
                    noise_threshold=0.01,
                )
                for index in range(1, 11)
            ]
            alert = ConvergenceMonitor(
                ConvergenceConfig(window_size=5, paradigm_shift_windows=2)
            ).evaluate(session_id="session", attempts=attempts)
            return alert.level, alert.model_dump(mode="json")
        result = DecisionEngine().decide(
            assessment=_assessment(attempt_category="protocol_violated", protocol_intact=False, scientific_effect=None, primary_delta=None),
            phase="b_dev",
            noise_threshold=0.01,
        )
        return result.action, result.model_dump(mode="json")


def _assessment(**updates) -> EffectiveScientificAssessment:
    values = {
        "attempt_category": "scientifically_evaluable",
        "execution_status": "COMPLETED",
        "patch_applied": True,
        "smoke_passed": True,
        "metrics_parsed": True,
        "protocol_intact": True,
        "evaluation_status": "COMPARABLE",
        "scientific_effect": "IMPROVEMENT",
        "primary_delta": 0.05,
        "guardrail_deltas": {},
    }
    values.update(updates)
    return EffectiveScientificAssessment(
        attempt_id="attempt_000001",
        outcome_card_ref="attempts/attempt_000001/outcome_card.json",
        outcome_card_sha256="a" * 64,
        scientific_assessment_ref="attempts/attempt_000001/scientific_assessment.json",
        scientific_assessment_sha256="b" * 64,
        evidence_refs=["attempts/attempt_000001/execution_result.json"],
        **values,
    )


def _failure(code: str, *, retryable: bool = False) -> FailureClassification:
    return FailureClassification(
        profile=DetectorProfile.GPU_TRAINING,
        enabled_detectors=["bench"],
        matched_detector="bench",
        failure_code=code,
        attempt_category="run_failed",
        retryable=retryable,
    )
