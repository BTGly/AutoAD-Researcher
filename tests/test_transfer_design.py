"""C25-C27: Transfer design — schema contracts and routing tests."""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from autoad_researcher.schemas.transfer_design import (
    AcceptedRisk,
    AlignableScope,
    AlignmentEntry,
    AlignmentStatus,
    CLASSIFICATION_RULES as ClassificationRules,
    CompatibilityDimension,
    CompatibilityStatus,
    ConstraintRef,
    ConstraintStrength,
    DerivedClaim,
    DimensionJudgment,
    DIMENSION_POLICY as DimensionPolicy,
    DIMENSION_POLICY,
    EvidenceStrategy,
    HookBinding,
    IdeaAspectRef,
    IdeaContract,
    IdeaTransferAnalysis,
    IdeaTransferDesignHandoff,
    ImplementationVariant,
    InterfaceContractDelta,
    PaperGroundedIdeaContract,
    PaperReanalysisRequest,
    RegimeChange,
    RejectedVariant,
    RepositoryReanalysisRequest,
    ResolutionClass,
    RiskRecord,
    SelectedVariant,
    SpawnChildRunRequest,
    StateChangeDescription,
    TensorContractDelta,
    TransferConstraint,
    TransferResumeFingerprint,
    TransferStatus,
    TransferValidationIssue,
    UnresolvedDimension,
    UserProvidedIdeaContract,
    VariantRiskReport,
    VariantSelection,
    VariantTransferAnalysis,
    compute_variant_risk,
    derive_variant_status,
    violates_confirmed_constraint,
)
from autoad_researcher.schemas.baseline_architecture import (
    ArchitectureComponent,
    BaselineArchitectureContract,
    ExecutionPhaseContract,
    InterfaceSpec,
    ModificationHook,
    TensorAxis,
    TensorSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _make_baseline_contract(hooks=None, components=None, phases=None, tensors=None):
    return BaselineArchitectureContract(
        model_name="PatchCore",
        repository_source_id="repo_001",
        repository_commit="a" * 64,
        architecture_components=components or [],
        phases=phases or [],
        tensors=tensors or [],
        modifiable_hooks=hooks or [],
    )


def _make_hook(
    hook_id="hook_001",
    path_classification="modifiable_candidate",
    allowed=True,
):
    return ModificationHook(
        hook_id=hook_id,
        hook_name=f"hook_{hook_id}",
        module_path="models/patchcore/torch_model.py",
        symbol="forward",
        semantic_role="feature_extraction",
        path_classification=path_classification,
        allowed_for_transfer_design=allowed,
    )


def _make_derived(value="test"):
    return DerivedClaim(value=value)


def _make_paper_idea():
    return PaperGroundedIdeaContract(
        paper_idea_source_id="src_001",
        paper_mechanism_summary="Cross-scale attention for feature fusion",
        paper_evidence_ids=["ev_paper_001"],
        original_mechanism_rationale=_make_derived("Works because multi-scale"),
        transfer_relevance=_make_derived("Relevant to AD feature extraction"),
    )


def _make_user_idea():
    return UserProvidedIdeaContract(
        user_description="My custom mechanism",
        user_evidence_ids=["ev_user_001"],
        mechanism_hypothesis=_make_derived("Should improve feature quality"),
        transfer_relevance=_make_derived("AD needs better features"),
    )


def _make_confirmed_idea(source=None):
    return IdeaContract(
        idea_id="idea_001",
        idea_source=source or _make_paper_idea(),
        confirmation_status="confirmed",
        confirmed_by_user_at=_now(),
        confirmation_evidence_id="ev_confirm_001",
    )


def _make_variant(variant_id="var_A", idea_id="idea_001", risk="medium"):
    return ImplementationVariant(
        variant_id=variant_id,
        variant_label=f"Variant {variant_id}",
        idea_id=idea_id,
        primary_hook_id="hook_001",
        hook_bindings=[HookBinding(hook_id="hook_001", role="primary_input", description="test")],
        risk_level=risk,
        fallback_behavior="revert",
        expected_behavior_rationale="Should work",
    )


def _make_judgment(variant_id="var_A", dim=CompatibilityDimension.SEMANTIC, status=CompatibilityStatus.COMPATIBLE):
    return DimensionJudgment(
        variant_id=variant_id,
        dimension=dim,
        status=status,
        blocking=(status == CompatibilityStatus.INCOMPATIBLE),
        reasoning=f"{dim.value}: {status.value}",
    )


# ---------------------------------------------------------------------------
# C25: Contract schemas
# ---------------------------------------------------------------------------


class TestIdeaContract:
    def test_paper_grounded_valid(self):
        c = _make_confirmed_idea()
        assert c.confirmation_status == "confirmed"
        assert c.idea_source.source == "paper_grounded"

    def test_user_provided_valid(self):
        c = IdeaContract(
            idea_id="idea_002",
            idea_source=_make_user_idea(),
            confirmation_status="pending",
        )
        assert c.idea_source.source == "user_provided"

    def test_confirmed_requires_timestamp(self):
        with pytest.raises(ValidationError, match="confirmed_by_user_at"):
            IdeaContract(
                idea_id="idea_003",
                idea_source=_make_user_idea(),
                confirmation_status="confirmed",
            )

    def test_confirmed_requires_evidence(self):
        with pytest.raises(ValidationError, match="confirmation_evidence_id"):
            IdeaContract(
                idea_id="idea_003",
                idea_source=_make_user_idea(),
                confirmation_status="confirmed",
                confirmed_by_user_at=_now(),
            )

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            IdeaContract(
                idea_id="idea_004",
                idea_source=_make_user_idea(),
                confirmation_status="pending",
                extra="no",  # type: ignore[call-arg]
            )


class TestDerivedClaim:
    def test_valid(self):
        dc = DerivedClaim(value="test claim")
        assert dc.status == "inferred"
        assert dc.producer_stage == "3.4"

    def test_supports_evidence_ids(self):
        dc = DerivedClaim(value="test", supporting_evidence_ids=["ev_001"])
        assert dc.supporting_evidence_ids == ["ev_001"]

    def test_assumptions(self):
        dc = DerivedClaim(value="test", assumptions=["assumption_1"])
        assert "assumption_1" in dc.assumptions


class TestConstraintRef:
    def test_valid(self):
        cr = ConstraintRef(description="must not change metric", source="user_provided")
        assert cr.source == "user_provided"

    def test_with_evidence(self):
        cr = ConstraintRef(description="x", source="paper_derived", evidence_ids=["ev_001"])
        assert cr.evidence_ids == ["ev_001"]


# ---------------------------------------------------------------------------
# C25: Baseline architecture schemas
# ---------------------------------------------------------------------------


class TestBaselineArchitectureContract:
    def test_minimal_valid(self):
        c = _make_baseline_contract()
        assert c.model_name == "PatchCore"

    def test_with_hooks(self):
        hooks = [_make_hook("hook_001"), _make_hook("hook_002")]
        c = _make_baseline_contract(hooks=hooks)
        assert len(c.modifiable_hooks) == 2

    def test_with_phases(self):
        phase = ExecutionPhaseContract(
            phase_id="phase_001",
            phase="infer",
            uses_gradient=False,
            mutates_model_state=False,
        )
        c = _make_baseline_contract(phases=[phase])
        assert len(c.phases) == 1
        assert c.phases[0].phase == "infer"

    def test_with_components(self):
        comp = ArchitectureComponent(
            component_id="comp_001",
            name="Backbone",
            role="feature_extractor",
            semantic_description="CNN backbone",
        )
        c = _make_baseline_contract(components=[comp])
        assert c.architecture_components[0].name == "Backbone"

    def test_commit_is_sha256(self):
        with pytest.raises(ValidationError):
            _make_baseline_contract().__class__(
                model_name="X",
                repository_source_id="r",
                repository_commit="not-sha256",
            )


class TestTensorSpec:
    def test_valid(self):
        ts = TensorSpec(
            tensor_name="features",
            rank=4,
            axes=[TensorAxis(name="B", semantic_role="batch", dynamic=True)],
            semantic_role="backbone_output",
        )
        assert ts.rank == 4

    def test_minimal(self):
        ts = TensorSpec(tensor_name="x", semantic_role="intermediate")
        assert ts.rank is None


class TestModificationHook:
    def test_valid(self):
        h = _make_hook()
        assert h.path_classification == "modifiable_candidate"
        assert h.allowed_for_transfer_design is True

    def test_protected_hook(self):
        h = _make_hook(hook_id="h_prot", path_classification="protected_candidate", allowed=False)
        assert h.path_classification == "protected_candidate"
        assert h.allowed_for_transfer_design is False

    def test_unknown_hook(self):
        h = _make_hook(hook_id="h_unk", path_classification="unknown")
        assert h.path_classification == "unknown"


# ---------------------------------------------------------------------------
# C25: Alignment schemas
# ---------------------------------------------------------------------------


class TestAlignmentEntry:
    def test_minimal_valid(self):
        aspect = IdeaAspectRef(
            aspect_id="asp_001",
            label="Test",
            description="Test aspect",
            source_kind="paper_grounded",
        )
        e = AlignmentEntry(
            idea_aspect=aspect,
            match_status=AlignmentStatus.COMPATIBLE,
            scope=AlignableScope.SPECIFIC_HOOK,
            rationale="test",
        )
        assert e.match_status == AlignmentStatus.COMPATIBLE

    def test_aspect_source_neutral(self):
        asp = IdeaAspectRef(
            aspect_id="asp_002",
            label="User Aspect",
            description="User described",
            source_kind="user_provided",
            evidence_ids=[],
        )
        assert asp.source_kind == "user_provided"
        assert asp.evidence_ids == []  # OK for user-provided

    def test_incompatible_global(self):
        aspect = IdeaAspectRef(aspect_id="a", label="x", description="x", source_kind="paper_grounded")
        e = AlignmentEntry(
            idea_aspect=aspect,
            match_status=AlignmentStatus.INCOMPATIBLE,
            scope=AlignableScope.GLOBAL_IDEA,
            rationale="no match",
        )
        assert e.scope == AlignableScope.GLOBAL_IDEA


# ---------------------------------------------------------------------------
# C26: Routing tests
# ---------------------------------------------------------------------------


class TestRouting:
    def test_user_idea_found_in_paper(self):
        from autoad_researcher.transfer.router import route_user_idea

        sources = [
            {"label": "Cross-Scale Attention", "source_id": "s1",
             "mechanism_summary": "Multi-scale attention for feature fusion",
             "mechanism_why": "Multi-scale fusion improves feature representation",
             "evidence_ids": ["ev1"]}
        ]
        result = route_user_idea("Cross-Scale Attention", sources, "ev_user_001")
        assert result.route == "A_paper_grounded"
        assert result.idea_source is not None
        assert isinstance(result.idea_source, PaperGroundedIdeaContract)

    def test_user_idea_not_found(self):
        from autoad_researcher.transfer.router import route_user_idea

        sources = [{"label": "Something Else", "source_id": "s1",
                    "mechanism_summary": "x", "evidence_ids": []}]
        result = route_user_idea("Nonexistent Method", sources, "ev_user_001")
        assert result.route == "A_not_found"
        assert result.blocked

    def test_user_original_idea(self):
        from autoad_researcher.transfer.router import route_user_original_idea

        result = route_user_original_idea("My custom approach", "ev_user_001")
        assert result.route == "A_user_provided"
        assert result.idea_source is not None

    def test_resolve_paper_candidates(self):
        from autoad_researcher.transfer.router import resolve_paper_candidates

        sources = [
            {"source_id": "s1", "mechanism_label": "Module A",
             "mechanism_summary": "Attention module", "evidence_ids": ["ev1"]},
            {"source_id": "s2", "mechanism_label": "Module B",
             "mechanism_summary": "Memory bank module", "evidence_ids": ["ev2"]},
        ]
        result = resolve_paper_candidates(sources, baseline_contract_hooks=["backbone_after", "memory_bank_insertion"])
        assert result.route == "B_candidates_ready"
        assert len(result.candidates) == 2

    def test_no_candidates_blocks(self):
        from autoad_researcher.transfer.router import resolve_paper_candidates

        result = resolve_paper_candidates([])
        assert result.blocked
        assert "No paper_idea_sources" in result.blocked_reason


# ---------------------------------------------------------------------------
# C27: Compatibility tests
# ---------------------------------------------------------------------------


class TestDimensionJudgment:
    def test_incompatible_must_be_blocking(self):
        j = _make_judgment(status=CompatibilityStatus.INCOMPATIBLE)
        assert j.blocking is True

    def test_incompatible_with_blocking_false_rejected(self):
        with pytest.raises(ValidationError, match="blocking=True"):
            DimensionJudgment(
                variant_id="var_A",
                dimension=CompatibilityDimension.SEMANTIC,
                status=CompatibilityStatus.INCOMPATIBLE,
                blocking=False,
                reasoning="x",
            )

    def test_compatible_cannot_be_blocking(self):
        with pytest.raises(ValidationError, match="cannot be blocking"):
            DimensionJudgment(
                variant_id="var_A",
                dimension=CompatibilityDimension.SEMANTIC,
                status=CompatibilityStatus.COMPATIBLE,
                blocking=True,
                reasoning="x",
            )


class TestDeriveVariantStatus:
    def test_all_compatible_returns_viable(self):
        judgments = [_make_judgment(dim=d, status=CompatibilityStatus.COMPATIBLE)
                     for d in CompatibilityDimension]
        status = derive_variant_status(judgments, [])
        assert status == TransferStatus.VIABLE

    def test_adapter_returns_viable_with_conditions(self):
        judgments = [_make_judgment(status=CompatibilityStatus.COMPATIBLE) for _ in range(8)]
        judgments.append(_make_judgment(dim=CompatibilityDimension.INPUT, status=CompatibilityStatus.COMPATIBLE_WITH_ADAPTER))
        status = derive_variant_status(judgments, [])
        assert status == TransferStatus.VIABLE_WITH_CONDITIONS

    def test_incompatible_returns_non_viable(self):
        judgments = [_make_judgment(status=CompatibilityStatus.COMPATIBLE) for _ in range(8)]
        judgments.append(_make_judgment(dim=CompatibilityDimension.DATA, status=CompatibilityStatus.INCOMPATIBLE))
        status = derive_variant_status(judgments, [])
        assert status == TransferStatus.NON_VIABLE

    def test_core_insufficient_evidence_needs_reanalysis(self):
        judgments = [_make_judgment(status=CompatibilityStatus.COMPATIBLE) for _ in range(8)]
        judgments.append(_make_judgment(dim=CompatibilityDimension.INPUT, status=CompatibilityStatus.INSUFFICIENT_EVIDENCE))
        status = derive_variant_status(judgments, [])
        assert status == TransferStatus.NEEDS_REANALYSIS

    def test_data_insufficient_evidence_design_blocking(self):
        judgments = [_make_judgment(status=CompatibilityStatus.COMPATIBLE) for _ in range(8)]
        judgments.append(_make_judgment(dim=CompatibilityDimension.DATA, status=CompatibilityStatus.INSUFFICIENT_EVIDENCE))
        status = derive_variant_status(judgments, [])
        # DATA has DESIGN_BLOCKING in DIMENSION_POLICY
        assert status == TransferStatus.NON_VIABLE

    def test_violates_constraint(self):
        constraint = TransferConstraint(
            constraint_id="c_001",
            category=CompatibilityDimension.TRAINING,
            description="No training allowed",
            strength=ConstraintStrength.USER_CONFIRMED,
            prohibited_changes=["add_fit_phase"],
        )
        judgment = DimensionJudgment(
            variant_id="var_A",
            dimension=CompatibilityDimension.TRAINING,
            status=CompatibilityStatus.REQUIRES_REGIME_CHANGE,
            blocking=False,
            reasoning="Needs training",
            required_changes=["add_fit_phase"],
        )
        assert violates_confirmed_constraint(judgment, [constraint]) is True

    def test_soft_constraint_not_violated(self):
        constraint = TransferConstraint(
            constraint_id="c_001",
            category=CompatibilityDimension.TRAINING,
            description="Soft suggestion",
            strength=ConstraintStrength.SOFT,
            prohibited_changes=["add_fit_phase"],
        )
        judgment = DimensionJudgment(
            variant_id="var_A",
            dimension=CompatibilityDimension.TRAINING,
            status=CompatibilityStatus.REQUIRES_REGIME_CHANGE,
            blocking=False,
            reasoning="Needs training",
            required_changes=["add_fit_phase"],
        )
        assert violates_confirmed_constraint(judgment, [constraint]) is False


class TestDIMENSION_POLICY:
    def test_all_dimensions_covered(self):
        for dim in CompatibilityDimension:
            assert dim in DimensionPolicy, f"Missing policy for {dim}"


# ---------------------------------------------------------------------------
# C28: Variant-Hook invariant tests
# ---------------------------------------------------------------------------


class TestHookBinding:
    def test_regular_binding(self):
        hb = HookBinding(hook_id="hook_001", role="primary_input", description="test")
        assert hb.hook_id == "hook_001"


class TestImplementationVariant:
    def test_valid_minimal(self):
        v = _make_variant()
        assert v.variant_id == "var_A"
        assert v.risk_level == "medium"

    def test_no_file_level_fields(self):
        v = _make_variant()
        # ImplementationVariant should NOT have files_to_modify, new_components etc.
        d = v.model_dump()
        assert "files_to_modify" not in d, "ImplementationVariant must not contain file-level fields"


# ---------------------------------------------------------------------------
# C29: Risk tests
# ---------------------------------------------------------------------------


class TestComputeVariantRisk:
    def test_all_low_returns_low(self):
        hooks = {"hook_001": _make_hook(path_classification="modifiable_candidate")}
        v = _make_variant()
        judgments = [DimensionJudgment(
            variant_id="var_A", dimension=CompatibilityDimension.SEMANTIC,
            status=CompatibilityStatus.COMPATIBLE, blocking=False, reasoning="ok", risk="low",
        )]
        result = compute_variant_risk(v, judgments, hooks)
        assert result == "low"

    def test_high_judgment_returns_high(self):
        hooks = {"hook_001": _make_hook()}
        v = _make_variant()
        judgments = [DimensionJudgment(
            variant_id="var_A", dimension=CompatibilityDimension.SEMANTIC,
            status=CompatibilityStatus.INCOMPATIBLE, blocking=True, reasoning="bad", risk="high",
        )]
        result = compute_variant_risk(v, judgments, hooks)
        assert result == "high"

    def test_protected_hook_returns_high(self):
        hooks = {"hook_001": _make_hook(path_classification="protected_candidate")}
        v = _make_variant()
        judgments: list[DimensionJudgment] = []
        result = compute_variant_risk(v, judgments, hooks)
        assert result == "high"

    def test_regime_change_adds_medium(self):
        hooks = {"hook_001": _make_hook()}
        v = ImplementationVariant(
            variant_id="var_B",
            variant_label="B",
            idea_id="idea_001",
            primary_hook_id="hook_001",
            hook_bindings=[HookBinding(hook_id="hook_001", role="primary_input", description="test")],
            risk_level="medium",
            fallback_behavior="revert",
            expected_behavior_rationale="x",
            regime_changes=[RegimeChange(phase_id="phase_001")],
        )
        judgments: list[DimensionJudgment] = []
        result = compute_variant_risk(v, judgments, hooks)
        assert result == "medium"


class TestAcceptedRisk:
    def test_valid(self):
        ar = AcceptedRisk(
            risk_id="risk_001",
            variant_id="var_A",
            severity="medium",
            accepted_by_user=True,
            user_decision_evidence_id="ev_accept_001",
            accepted_at=_now(),
        )
        assert ar.accepted_by_user is True

    def test_invalid_severity(self):
        with pytest.raises(ValidationError):
            AcceptedRisk(
                risk_id="risk_001",
                variant_id="var_A",
                severity="low",  # type: ignore[arg-type]
                accepted_by_user=True,
                user_decision_evidence_id="ev_001",
                accepted_at=_now(),
            )


# ---------------------------------------------------------------------------
# C30: VariantSelection tests
# ---------------------------------------------------------------------------


class TestVariantSelection:
    def test_valid_pending(self):
        vs = VariantSelection(
            selection_id="sel_001",
            idea_id="idea_001",
            confirmation_status="pending",
        )
        assert vs.confirmation_status == "pending"

    def test_confirmed_requires_selections(self):
        with pytest.raises(ValidationError, match="at least one"):
            VariantSelection(
                selection_id="sel_001",
                idea_id="idea_001",
                confirmation_status="confirmed",
            )

    def test_selected_and_rejected_same_variant(self):
        with pytest.raises(ValidationError, match="both selected and rejected"):
            VariantSelection(
                selection_id="sel_001",
                idea_id="idea_001",
                selected=[SelectedVariant(
                    variant_id="var_A", user_decision_evidence_id="ev_001", selected_at=_now(),
                )],
                rejected=[RejectedVariant(variant_id="var_A", reason="user_rejected")],
                confirmation_status="confirmed",
            )


# ---------------------------------------------------------------------------
# C31: UnresolvedDimension tests
# ---------------------------------------------------------------------------


class TestUnresolvedDimension:
    def test_experiment_resolvable_needs_verification(self):
        with pytest.raises(ValidationError, match="verification_target"):
            UnresolvedDimension(
                variant_id="var_A",
                dimension=CompatibilityDimension.RESOURCE,
                status=CompatibilityStatus.INSUFFICIENT_EVIDENCE,
                classification=ResolutionClass.EXPERIMENT_RESOLVABLE,
                resolution_reason="need to measure",
                classified_by_rule_id="rule_001",
            )

    def test_experiment_resolvable_with_verification(self):
        u = UnresolvedDimension(
            variant_id="var_A",
            dimension=CompatibilityDimension.RESOURCE,
            status=CompatibilityStatus.INSUFFICIENT_EVIDENCE,
            classification=ResolutionClass.EXPERIMENT_RESOLVABLE,
            resolution_reason="need to measure",
            verification_target="Measure peak GPU memory",
            acceptance_criterion="< 16GB",
            classified_by_rule_id="rule_001",
        )
        assert u.classification == ResolutionClass.EXPERIMENT_RESOLVABLE

    def test_design_blocking_no_verification_needed(self):
        u = UnresolvedDimension(
            variant_id="var_A",
            dimension=CompatibilityDimension.DATA,
            status=CompatibilityStatus.INCOMPATIBLE,
            classification=ResolutionClass.DESIGN_BLOCKING,
            resolution_reason="data conflict",
            classified_by_rule_id="rule_data",
        )
        assert u.classification == ResolutionClass.DESIGN_BLOCKING
        assert u.verification_target is None  # not required for design_blocking


class TestCLASSIFICATION_RULES:
    def test_rules_defined(self):
        assert len(ClassificationRules) >= 8

    def test_label_incompatible_is_design_blocking(self):
        key = (CompatibilityDimension.LABEL, CompatibilityStatus.INCOMPATIBLE)
        assert ClassificationRules[key] == ResolutionClass.DESIGN_BLOCKING


# ---------------------------------------------------------------------------
# C32: Validator tests
# ---------------------------------------------------------------------------


class TestValidator:
    def test_passing_validation(self):
        from autoad_researcher.transfer.validator import validate_transfer

        hooks = {"hook_001": _make_hook()}
        idea = _make_confirmed_idea()
        baseline = _make_baseline_contract(hooks=[_make_hook()])
        v = ImplementationVariant(
            variant_id="var_A",
            variant_label="Variant A",
            idea_id="idea_001",
            primary_hook_id="hook_001",
            hook_bindings=[HookBinding(hook_id="hook_001", role="primary_input", description="test")],
            risk_level="low",
            fallback_behavior="revert",
            expected_behavior_rationale="Should work",
        )
        variants = [v]
        judgments = [DimensionJudgment(
            variant_id="var_A", dimension=CompatibilityDimension.SEMANTIC,
            status=CompatibilityStatus.COMPATIBLE, blocking=False, reasoning="ok",
        )]
        va = VariantTransferAnalysis(
            variant_id="var_A",
            dimensions=judgments,
            overall_status=TransferStatus.VIABLE,
        )
        analysis = IdeaTransferAnalysis(
            idea_id="idea_001",
            variant_analyses={"var_A": va},
            viable_variant_ids=["var_A"],
        )
        selection = VariantSelection(
            selection_id="sel_001",
            idea_id="idea_001",
            selected=[SelectedVariant(
                variant_id="var_A", user_decision_evidence_id="ev_001", selected_at=_now(),
            )],
            confirmation_status="confirmed",
        )
        risk_reports = [VariantRiskReport(
            variant_id="var_A", computed_risk_level="low",
        )]

        report = validate_transfer(
            run_id="run_001",
            idea_contract=idea,
            baseline_contract=baseline,
            analysis=analysis,
            selection=selection,
            variants=[v],
            risk_reports=risk_reports,
            hooks=hooks,
            resolved_dimensions=[],
        )
        assert report.status == "passed"

    def test_protected_hook_rejected(self):
        from autoad_researcher.transfer.validator import validate_transfer

        hooks = {"hook_001": _make_hook(path_classification="protected_candidate", allowed=False)}
        idea = _make_confirmed_idea()
        baseline = _make_baseline_contract(hooks=[hooks["hook_001"]])
        variants = [_make_variant()]
        analysis = IdeaTransferAnalysis(idea_id="idea_001", variant_analyses={})
        selection = VariantSelection(selection_id="sel_001", idea_id="idea_001", confirmation_status="pending")
        risk_reports: list[VariantRiskReport] = []

        report = validate_transfer(
            run_id="run_001",
            idea_contract=idea,
            baseline_contract=baseline,
            analysis=analysis,
            selection=selection,
            variants=variants,
            risk_reports=risk_reports,
            hooks=hooks,
            resolved_dimensions=[],
        )
        assert report.status in ("failed", "partial_repair_successful")
        assert any("protected_candidate" in i.description for i in report.issues)

    def test_incompatible_blocking_false_rejected_at_schema_level(self):
        """INCOMPATIBLE + blocking=false is rejected by Pydantic validator, not at runtime."""
        with pytest.raises(ValidationError, match="blocking=True"):
            DimensionJudgment(
                variant_id="var_A", dimension=CompatibilityDimension.SEMANTIC,
                status=CompatibilityStatus.INCOMPATIBLE,
                blocking=False,
                reasoning="x",
            )

    def test_risk_level_mismatch_rejected(self):
        from autoad_researcher.transfer.validator import validate_transfer

        hooks = {"hook_001": _make_hook()}
        idea = _make_confirmed_idea()
        baseline = _make_baseline_contract(hooks=[hooks["hook_001"]])
        variants = [_make_variant(risk="low")]  # claims low
        judgments = [DimensionJudgment(
            variant_id="var_A", dimension=CompatibilityDimension.SEMANTIC,
            status=CompatibilityStatus.INCOMPATIBLE, blocking=True, reasoning="x", risk="high",
        )]
        va = VariantTransferAnalysis(
            variant_id="var_A", dimensions=judgments, overall_status=TransferStatus.NON_VIABLE,
        )
        analysis = IdeaTransferAnalysis(idea_id="idea_001", variant_analyses={"var_A": va})
        selection = VariantSelection(selection_id="sel_001", idea_id="idea_001", confirmation_status="pending")
        risk_reports = [VariantRiskReport(
            variant_id="var_A", computed_risk_level="high",
        )]

        report = validate_transfer(
            run_id="run_001",
            idea_contract=idea,
            baseline_contract=baseline,
            analysis=analysis,
            selection=selection,
            variants=variants,
            risk_reports=risk_reports,
            hooks=hooks,
            resolved_dimensions=[],
        )
        assert any("risk_level" in i.description.lower() for i in report.issues)


# ---------------------------------------------------------------------------
# C33: Reanalysis and handoff tests
# ---------------------------------------------------------------------------


class TestReanalysisRequests:
    def test_build_repository_reanalysis(self):
        from autoad_researcher.transfer.reanalysis import build_repository_reanalysis

        req = build_repository_reanalysis(
            run_id="run_001",
            reason="Missing baseline contract",
            missing_artifacts=["baseline_architecture_contract.json"],
        )
        assert req.run_id == "run_001"
        assert len(req.missing_artifacts) == 1

    def test_build_paper_reanalysis(self):
        from autoad_researcher.transfer.reanalysis import build_paper_reanalysis

        req = build_paper_reanalysis(
            run_id="run_001",
            reason="Missing evidence",
            target_method_ids=["method_001"],
        )
        assert req.run_id == "run_001"

    def test_build_spawn_child_run(self):
        from autoad_researcher.transfer.reanalysis import build_spawn_child_run

        req = build_spawn_child_run(
            parent_run_id="run_001",
            reason="parent_idea_non_viable",
        )
        assert req.parent_run_id == "run_001"

    def test_spawn_child_invalid_reason(self):
        from autoad_researcher.transfer.reanalysis import build_spawn_child_run

        with pytest.raises(ValueError):
            build_spawn_child_run(parent_run_id="run_001", reason="invalid_reason")


class TestHandoff:
    def test_build_handoff(self):
        from autoad_researcher.transfer.handoff import build_handoff

        idea = _make_confirmed_idea()
        analysis = IdeaTransferAnalysis(idea_id="idea_001", variant_analyses={})
        variants = [_make_variant()]
        risk_report = VariantRiskReport(variant_id="var_A", computed_risk_level="low")

        handoff = build_handoff(
            run_id="run_001",
            source_context_id="ctx_001",
            source_context_version=0,
            source_context_sha256="a" * 64,
            idea_contract=idea,
            transfer_analysis=analysis,
            transfer_constraints=[],
            selected_variants=variants,
            risk_reports=[risk_report],
            unresolved_dimensions=[],
            validator_report_sha256="b" * 64,
        )
        assert handoff.next_stage == "3.5_multi_variant_experiment_planner"
        assert len(handoff.idea_contract_sha256) == 64

    def test_design_blocking_rejected_from_handoff(self):
        from autoad_researcher.transfer.handoff import build_handoff

        blocking = UnresolvedDimension(
            variant_id="var_A",
            dimension=CompatibilityDimension.DATA,
            status=CompatibilityStatus.INCOMPATIBLE,
            classification=ResolutionClass.DESIGN_BLOCKING,
            resolution_reason="data conflict",
            classified_by_rule_id="rule_data",
        )
        with pytest.raises(ValueError, match="design_blocking"):
            build_handoff(
                run_id="run_001",
                source_context_id="ctx_001",
                source_context_version=0,
                source_context_sha256="a" * 64,
                idea_contract=_make_confirmed_idea(),
                transfer_analysis=IdeaTransferAnalysis(idea_id="idea_001", variant_analyses={}),
                transfer_constraints=[],
                selected_variants=[_make_variant()],
                risk_reports=[],
                unresolved_dimensions=[blocking],
                validator_report_sha256="b" * 64,
            )


# ---------------------------------------------------------------------------
# C32-extra: Selector tests
# ---------------------------------------------------------------------------


class TestSelector:
    def test_recommend_does_not_auto_select(self):
        from autoad_researcher.transfer.selector import recommend_variants

        selection = recommend_variants(
            variants=[_make_variant("var_A"), _make_variant("var_B")],
            presentable_ids=["var_A", "var_B"],
            non_viable_ids=[],
            needs_reanalysis_ids=[],
            idea_id="idea_001",
        )
        assert len(selection.selected) == 0
        assert selection.confirmation_status == "pending"
        assert selection.recommended_variant_ids == ["var_A", "var_B"]

    def test_select_variants(self):
        from autoad_researcher.transfer.selector import recommend_variants, select_variants

        selection = recommend_variants(
            variants=[_make_variant("var_A")],
            presentable_ids=["var_A"],
            non_viable_ids=[],
            needs_reanalysis_ids=[],
            idea_id="idea_001",
        )
        selection = select_variants(selection, ["var_A"], "ev_select_001")
        assert len(selection.selected) == 1
        assert selection.confirmation_status == "confirmed"

    def test_reject_risk_removes_variant(self):
        from autoad_researcher.transfer.selector import recommend_variants, reject_risk_from_selection, select_variants

        selection = recommend_variants(
            variants=[_make_variant("var_A")],
            presentable_ids=["var_A"],
            non_viable_ids=[],
            needs_reanalysis_ids=[],
            idea_id="idea_001",
        )
        selection = select_variants(selection, ["var_A"], "ev_select_001")
        selection = reject_risk_from_selection(selection, "var_A")
        assert len(selection.selected) == 0
        assert any(r.variant_id == "var_A" and r.reason == "user_rejected" for r in selection.rejected)


# ---------------------------------------------------------------------------
# C32-extra: Source-neutral alignment tests
# ---------------------------------------------------------------------------


class TestSourceNeutralAlignment:
    def test_user_provided_aspect(self):
        aspect = IdeaAspectRef(
            aspect_id="asp_user",
            label="User Idea",
            description="My idea",
            source_kind="user_provided",
            evidence_ids=[],
        )
        assert aspect.evidence_ids == []

    def test_derived_hypothesis_aspect(self):
        aspect = IdeaAspectRef(
            aspect_id="asp_hyp",
            label="Hypothesis",
            description="A derived hypothesis",
            source_kind="derived_hypothesis",
            evidence_ids=["ev_001"],
        )
        assert aspect.source_kind == "derived_hypothesis"


class TestAlignableScope:
    def test_all_scopes_defined(self):
        assert AlignableScope.GLOBAL_IDEA
        assert AlignableScope.SPECIFIC_HOOK
        assert AlignableScope.SPECIFIC_PHASE
        assert AlignableScope.SPECIFIC_VARIANT_ROUTE
