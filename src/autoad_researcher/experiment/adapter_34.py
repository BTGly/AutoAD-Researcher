"""Stage34InputAdapter — translates 3.4 sealed handoff into 3.5 internal input.

Fail-closed: any missing analysis, risk report, or non-viable status raises
``Stage34HandoffError``.  No silent continuation.
"""

import hashlib

from autoad_researcher.schemas.experiment_planning import (
    PreparationPhase,
    Stage35Input,
    Stage35VariantInput,
)
from autoad_researcher.schemas.transfer_design import (
    IdeaTransferDesignHandoff,
    ImplementationVariant,
)


class Stage34HandoffError(Exception):
    """3.4 handoff 结构不满足 3.5 要求时抛出。"""


class Stage34InputAdapter:
    """将 3.4 正式 handoff 翻译为 3.5 内部输入。

    每个 selected variant 必须存在对应的 VariantTransferAnalysis 和
    VariantRiskReport。缺失 → Stage34HandoffError，不静默继续。
    """

    def load(self, handoff: IdeaTransferDesignHandoff) -> Stage35Input:
        analyses = handoff.transfer_analysis.variant_analyses
        risk_by_id: dict[str, object] = {}
        for r in handoff.variant_risk_reports:
            if r.variant_id in risk_by_id:
                raise Stage34HandoffError(
                    f"duplicate VariantRiskReport.variant_id: {r.variant_id}"
                )
            risk_by_id[r.variant_id] = r

        if not handoff.selected_variants:
            raise Stage34HandoffError("selected_variants must not be empty")

        variant_inputs: list[Stage35VariantInput] = []
        for v in handoff.selected_variants:
            analysis = analyses.get(v.variant_id)
            if analysis is None:
                raise Stage34HandoffError(
                    f"selected variant {v.variant_id} has no VariantTransferAnalysis"
                )
            if analysis.variant_id != v.variant_id:
                raise Stage34HandoffError(
                    f"analysis.variant_id {analysis.variant_id} != variant {v.variant_id}"
                )
            if analysis.overall_status in {"non_viable", "needs_reanalysis"}:
                raise Stage34HandoffError(
                    f"selected variant {v.variant_id} has non-viable status "
                    f"{analysis.overall_status}"
                )
            risk = risk_by_id.get(v.variant_id)
            if risk is None:
                raise Stage34HandoffError(
                    f"selected variant {v.variant_id} has no VariantRiskReport"
                )

            experiment_resolvable = [
                u for u in handoff.experiment_resolvable_dimensions
                if u.variant_id == v.variant_id
            ]

            variant_inputs.append(Stage35VariantInput(
                variant=v,
                transfer_analysis=analysis,
                risk_report=risk,
                experiment_resolvable=experiment_resolvable,
            ))

        return Stage35Input(
            run_id=handoff.run_id,
            confirmed_idea=handoff.confirmed_idea,
            transfer_analysis=handoff.transfer_analysis,
            transfer_constraints=handoff.transfer_constraints,
            variants=variant_inputs,
            nonblocking_warnings=handoff.nonblocking_warnings,
        )


def derive_preparation_phase(variant: ImplementationVariant) -> PreparationPhase:
    """从 3.4 regime_changes 推导 preparation_phase 分类。

    Used by the Stage34InputAdapter to classify the preparation need per variant,
    avoiding the over-broad ``state_mutation_required`` trigger that would
    wrongly flag infer_init / online_state / postprocess mutations as training.
    """
    has_gradient = False
    has_training_phase = False
    has_state_mutation = False
    state_phase: str | None = None

    for change in variant.regime_changes:
        if change.gradient_required:
            has_gradient = True
        if change.after_phase is not None and change.after_phase.phase in {"fit", "train"}:
            has_training_phase = True
        if change.state_mutation_required:
            has_state_mutation = True
            if change.after_phase is not None:
                state_phase = change.after_phase.phase

    if has_gradient:
        return PreparationPhase.FIT
    if has_training_phase:
        return PreparationPhase.TRAIN
    if has_state_mutation:
        if state_phase == "infer":
            return PreparationPhase.INFER_INIT
        if state_phase == "postprocess":
            return PreparationPhase.ONLINE_STATE
        return PreparationPhase.ONLINE_STATE
    return PreparationPhase.NONE


def compute_unresolved_dimension_id(
    variant_id: str,
    dimension: str,
    observation_source: str,
) -> str:
    """Generate canonical SHA-256 for unresolved_dimension_id.

    stable across serialization rounds.
    """
    raw = f"{variant_id}::{dimension}::{observation_source}"
    return hashlib.sha256(raw.encode()).hexdigest()
