"""Paper Intelligence analysis and synthesis agent.

Implements the core paper intelligence loop:
- Skill-guided paper analysis (P6)
- Structured artifact synthesis (P7)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.paper_intelligence.control_models import (
    AnalysisProgress,
    PaperAnalysisControlSignal,
    PaperIntelligenceStatus,
)
from autoad_researcher.paper_intelligence.errors import (
    PaperIntelligenceContractError,
    PaperRepairExhaustedError,
)
from autoad_researcher.paper_intelligence.models import (
    MethodComponent,
    PaperAgentBudget,
    PaperClaim,
    PaperIdeaSourceCandidate,
    PaperIntelligenceRequest,
    PaperMentionedCandidate,
    PaperReaderResult,
    PaperSummary,
    RepositoryLinkCandidate,
)
from autoad_researcher.paper_intelligence.validator import (
    PaperValidationReport,
    validate_candidate,
    validate_claim,
)

# ---------------------------------------------------------------------------
# Analysis Observation
# ---------------------------------------------------------------------------


@dataclass
class AnalysisObservation:
    """A single, evidence-backed observation from the analysis agent."""

    observation_id: str
    cycle: int
    subject: str
    finding: str
    evidence_ids: list[str] = field(default_factory=list)
    confidence: str = "medium"


# ---------------------------------------------------------------------------
# Analysis Agent
# ---------------------------------------------------------------------------


@dataclass
class PaperAnalysisResult:
    """Result of a paper analysis cycle."""

    control_signal: PaperAnalysisControlSignal
    observations: list[AnalysisObservation] = field(default_factory=list)
    new_evidence_ids: list[str] = field(default_factory=list)


class PaperAnalysisAgent:
    """Skill-guided paper analysis agent.

    Manages the analysis loop: loading the paper-analysis skill,
    routing model calls, reading paper sections, extracting claims,
    and generating control signals per cycle.

    In this deterministic implementation, the agent follows a
    structured reading plan and does not require real LLM calls
    for the contract layer. Real LLM integration comes through
    the AgentHarness.
    """

    COVERAGE_TARGETS = [
        "research_problem",
        "proposed_method",
        "core_components",
        "data_assumptions",
        "training_objective",
        "experiment_setup",
        "baseline_candidates",
        "dataset_candidates",
        "metric_candidates",
        "transfer_points",
    ]

    def __init__(
        self,
        request: PaperIntelligenceRequest,
        budget: PaperAgentBudget,
        runs_dir: Path,
    ):
        self.request = request
        self.budget = budget
        self.runs_dir = runs_dir
        self._observations: list[AnalysisObservation] = []
        self._claims: list[PaperClaim] = []
        self._candidates: list[PaperMentionedCandidate] = []
        self._cycle = 0
        self._llm_calls = 0
        self._coverage: dict[str, str] = {
            t: "not_checked" for t in self.COVERAGE_TARGETS
        }
        self._no_progress_count = 0

    def run_analysis_cycle(self, previous_observations: list[AnalysisObservation] | None = None) -> PaperAnalysisResult:
        """Run one analysis cycle. Returns a control signal.

        In a real implementation this would route to the LLM with the
        paper-analysis skill. The deterministic skeleton checks coverage
        and simulates progress.
        """
        if self._cycle >= self.budget.max_analysis_llm_calls:
            return self._force_synthesis("LLM call budget exhausted")

        self._cycle += 1
        self._llm_calls += 1

        # Simulate discovering coverage
        newly_covered = 0
        for target in self.COVERAGE_TARGETS:
            if self._coverage[target] == "not_checked":
                self._coverage[target] = "confirmed"
                newly_covered += 1
                if newly_covered >= 2:
                    break

        if newly_covered == 0:
            self._no_progress_count += 1
        else:
            self._no_progress_count = 0

        if self._no_progress_count >= self.budget.max_no_progress_cycles:
            return self._force_synthesis("No progress limit reached")

        all_covered = all(
            v != "not_checked" for v in self._coverage.values()
        )

        if all_covered:
            return PaperAnalysisResult(
                control_signal=PaperAnalysisControlSignal(
                    decision="synthesis_ready",
                    coverage=self._coverage,
                    new_evidence_count=0,
                ),
                observations=self._observations,
            )

        return PaperAnalysisResult(
            control_signal=PaperAnalysisControlSignal(
                decision="continue_reading",
                coverage=self._coverage,
                new_evidence_count=newly_covered,
            ),
            observations=self._observations,
        )

    def _force_synthesis(self, reason: str) -> PaperAnalysisResult:
        """Force transition to synthesis state."""
        return PaperAnalysisResult(
            control_signal=PaperAnalysisControlSignal(
                decision="synthesis_ready",
                coverage=self._coverage,
                new_evidence_count=0,
                unresolved_blockers=[reason],
            ),
        )


# ---------------------------------------------------------------------------
# Synthesis Agent
# ---------------------------------------------------------------------------


@dataclass
class SynthesizedPaperArtifacts:
    """Container for all synthesized paper artifacts."""

    paper_summary: PaperSummary
    method_components: list[MethodComponent]
    paper_candidates: list[PaperMentionedCandidate]
    paper_uncertainties: list[dict]
    paper_idea_sources: list[PaperIdeaSourceCandidate]
    repository_link_candidates: list[RepositoryLinkCandidate]
    paper_reader_result: PaperReaderResult


class PaperArtifactSynthesizer:
    """Convert analysis observations into structured paper artifacts.

    Takes claims, candidates, and observations from analysis and
    assembles the seven formal paper artifacts per the 3.2 contracts.
    """

    def __init__(self, run_id: str, source_id: str, artifacts_dir: Path):
        self.run_id = run_id
        self.source_id = source_id
        self.artifacts_dir = artifacts_dir
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def synthesize(
        self,
        claims: list[PaperClaim],
        candidates: list[PaperMentionedCandidate],
        components: list[MethodComponent],
        idea_sources: list[PaperIdeaSourceCandidate],
        repo_links: list[RepositoryLinkCandidate],
        status: str,
        warnings: list[str] | None = None,
    ) -> SynthesizedPaperArtifacts:
        """Synthesize all paper artifacts from analysis outputs."""
        import json

        # Build paper_summary
        title = next((c for c in claims if c.subject == "title"), None)
        if title is None:
            title = PaperClaim(
                claim_id="cl_title",
                subject="title",
                predicate="is",
                value="Unknown",
                status="unknown",
                confidence="low",
            )

        summary = PaperSummary(
            schema_version=1,
            source_id=self.source_id,
            title=title,
            research_problem=[c for c in claims if c.subject in ("research_problem", "problem")],
            proposed_method=[c for c in claims if c.subject in ("proposed_method", "method")],
            core_components=[c for c in claims if c.subject == "component"],
            training_objective=[c for c in claims if c.subject == "training_objective"],
            data_assumptions=[c for c in claims if c.subject == "data_assumptions"],
            label_assumptions=[c for c in claims if c.subject == "label_assumptions"],
            inference_procedure=[c for c in claims if c.subject == "inference_procedure"],
            contributions=[c for c in claims if c.subject == "contribution"],
            stated_limitations=[c for c in claims if c.subject == "limitation"],
            potential_transfer_points=[c for c in claims if c.subject == "transfer_point"],
        )

        # Build uncertainties
        uncertainties: list[dict] = []
        for claim in claims:
            if claim.status in ("unknown", "conflicting"):
                uncertainties.append({
                    "claim_id": claim.claim_id,
                    "subject": claim.subject,
                    "status": claim.status,
                    "reason": claim.rationale_summary or "insufficient evidence",
                })

        # Build reader_result
        result = PaperReaderResult(
            schema_version=1,
            run_id=self.run_id,
            status=status,
            paper_summary_path=str(self.artifacts_dir / "paper_summary.json"),
            method_components_path=str(self.artifacts_dir / "method_components.json"),
            paper_candidates_path=str(self.artifacts_dir / "paper_candidates.json"),
            paper_uncertainties_path=str(self.artifacts_dir / "paper_uncertainties.json"),
            paper_idea_sources_path=str(self.artifacts_dir / "paper_idea_sources.json"),
            repository_link_candidates_path=str(self.artifacts_dir / "repository_link_candidates.json"),
            validation_report_path=str(self.artifacts_dir.parent / "validation" / "paper_validation_report.json"),
            warnings=warnings or [],
        )

        # Write artifacts
        self._write_json(summary.model_dump(), "paper_summary.json")
        self._write_json([c.model_dump() for c in components], "method_components.json")
        self._write_json([c.model_dump() for c in candidates], "paper_candidates.json")
        self._write_json(uncertainties, "paper_uncertainties.json")
        self._write_json([i.model_dump() for i in idea_sources], "paper_idea_sources.json")
        self._write_json([r.model_dump() for r in repo_links], "repository_link_candidates.json")
        self._write_json(result.model_dump(), "paper_reader_result.json")

        return SynthesizedPaperArtifacts(
            paper_summary=summary,
            method_components=components,
            paper_candidates=candidates,
            paper_uncertainties=uncertainties,
            paper_idea_sources=idea_sources,
            repository_link_candidates=repo_links,
            paper_reader_result=result,
        )

    def _write_json(self, data: Any, filename: str) -> None:
        import json
        path = self.artifacts_dir / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------


def budget_for_profile(profile: str) -> PaperAgentBudget:
    """Return a predefined budget for the given profile name."""
    profiles: dict[str, PaperAgentBudget] = {
        "short": PaperAgentBudget(
            max_total_tool_calls=24,
            max_total_llm_calls=8,
            max_total_input_tokens=70000,
            max_total_output_tokens=8000,
            max_parse_attempts=1,
            max_analysis_llm_calls=3,
            max_analysis_reads=18,
            max_analysis_search_calls=8,
            max_web_fetch_calls=0,
            max_repair_tool_calls=6,
            max_repair_llm_calls=1,
            max_repairs=1,
        ),
        "standard": PaperAgentBudget(
            max_total_tool_calls=44,
            max_total_llm_calls=14,
            max_total_input_tokens=130000,
            max_total_output_tokens=14000,
            max_parse_attempts=3,
            max_analysis_llm_calls=4,
            max_analysis_reads=34,
            max_analysis_search_calls=16,
            max_web_fetch_calls=10,
            max_repair_tool_calls=10,
            max_repair_llm_calls=2,
            max_repairs=2,
        ),
        "long": PaperAgentBudget(
            max_total_tool_calls=70,
            max_total_llm_calls=22,
            max_total_input_tokens=220000,
            max_total_output_tokens=22000,
            max_parse_attempts=3,
            max_analysis_llm_calls=6,
            max_analysis_reads=56,
            max_analysis_search_calls=26,
            max_web_fetch_calls=10,
            max_repair_tool_calls=14,
            max_repair_llm_calls=2,
            max_repairs=2,
        ),
    }
    if profile not in profiles:
        raise PaperIntelligenceContractError(f"Unknown budget profile: {profile}")
    return profiles[profile]
