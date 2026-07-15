from __future__ import annotations

from pathlib import Path

from autoad_researcher.assistant.v2.contract_hashing import confirmation_draft_sha256
from autoad_researcher.assistant.v2.intent_contract import ResearchIntentContract, load_contract_draft, save_contract_draft
from autoad_researcher.assistant.v2.mutation_protocol import (
    ContractMutationProposal,
    EvidenceSpan,
    FieldMutation,
    apply_contract_mutation,
)


def _span(user_input: str, text: str) -> EvidenceSpan:
    start = user_input.index(text)
    return EvidenceSpan(start=start, end=start + len(text), text=text)


def _operation(user_input: str, operation: str, target: str, value, evidence: str) -> FieldMutation:
    return FieldMutation(
        operation=operation,
        target=target,
        proposed_value=value,
        evidence_spans=[_span(user_input, evidence)],
        confidence=0.95,
    )


def test_applies_multiple_operations_as_one_hash_bound_write(tmp_path: Path):
    run_dir = tmp_path / "run_atomic"
    run_dir.mkdir()
    user_input = "目标改成复现 Model-X，只看 Metric-Z。"
    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256=None,
            full_turn_mutation_evidence=user_input,
            operations=[
                _operation(user_input, "set", "research_goal", "复现 Model-X", "复现 Model-X"),
                _operation(user_input, "set", "primary_metrics", ["Metric-Z"], "只看 Metric-Z"),
            ],
        ),
    )

    assert receipt.status == "applied"
    assert receipt.changed_fields == ["research_goal", "primary_metrics"]
    assert receipt.before_draft_sha256 is None
    assert receipt.after_draft_sha256
    durable = load_contract_draft(run_dir)
    assert durable is not None
    assert durable.research_goal == "复现 Model-X"
    assert durable.primary_metrics == ["Metric-Z"]
    assert durable.schema_version == 2
    assert durable.authorization_schema_version == 3
    assert durable.task_domain is None
    assert durable.allowed_change_scope == []
    assert durable.forbidden_change_scope == []
    assert "change_metric_definition" in durable.system_safety_policy


def test_rejects_stale_hash_without_partial_changes(tmp_path: Path):
    run_dir = tmp_path / "run_stale"
    original = ResearchIntentContract(run_id=run_dir.name, research_goal="旧目标", primary_metrics=["old"])
    save_contract_draft(run_dir, original)
    user_input = "换成新目标和新指标。"

    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256="0" * 64,
            full_turn_mutation_evidence=user_input,
            operations=[
                _operation(user_input, "replace", "research_goal", "新目标", "新目标"),
                _operation(user_input, "replace", "primary_metrics", ["new"], "新指标"),
            ],
        ),
    )

    assert receipt.status == "rejected"
    assert receipt.reason == "draft_hash_mismatch"
    assert load_contract_draft(run_dir) == original


def test_rejects_one_bad_span_without_applying_any_operation(tmp_path: Path):
    run_dir = tmp_path / "run_bad_span"
    original = ResearchIntentContract(run_id=run_dir.name, research_goal="旧目标")
    save_contract_draft(run_dir, original)
    user_input = "目标改成新目标。"
    operation = _operation(user_input, "replace", "research_goal", "新目标", "新目标")
    operation.evidence_spans[0].text = "伪造证据"

    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256=confirmation_draft_sha256(original),
            full_turn_mutation_evidence=user_input,
            operations=[operation],
        ),
    )

    assert receipt.status == "rejected"
    assert receipt.reason == "invalid_evidence_span"
    assert load_contract_draft(run_dir) == original


def test_replace_and_remove_do_not_merge_old_list_values(tmp_path: Path):
    run_dir = tmp_path / "run_correction"
    original = ResearchIntentContract(
        run_id=run_dir.name,
        primary_metrics=["latency", "throughput"],
        user_improvement_hints=["quantization"],
    )
    save_contract_draft(run_dir, original)
    user_input = "不看吞吐量，只看峰值显存；不要量化。"

    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256=confirmation_draft_sha256(original),
            full_turn_mutation_evidence=user_input,
            operations=[
                _operation(user_input, "replace", "primary_metrics", ["peak_vram"], "不看吞吐量，只看峰值显存"),
                _operation(user_input, "remove", "user_improvement_hints", None, "不要量化"),
            ],
        ),
    )

    assert receipt.status == "applied"
    durable = load_contract_draft(run_dir)
    assert durable is not None
    assert durable.primary_metrics == ["peak_vram"]
    assert durable.user_improvement_hints == []


def test_set_cannot_overwrite_nonempty_field(tmp_path: Path):
    run_dir = tmp_path / "run_set"
    original = ResearchIntentContract(run_id=run_dir.name, research_goal="旧目标")
    save_contract_draft(run_dir, original)
    user_input = "目标是新目标。"

    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256=confirmation_draft_sha256(original),
            full_turn_mutation_evidence=user_input,
            operations=[_operation(user_input, "set", "research_goal", "新目标", "新目标")],
        ),
    )

    assert receipt.status == "rejected"
    assert receipt.reason == "set_requires_empty_target"
    assert load_contract_draft(run_dir) == original


def test_task_profile_material_and_system_policy_are_not_intent_mutation_targets(tmp_path: Path):
    run_dir = tmp_path / "run_boundary"
    user_input = "Model-X"
    run_dir.mkdir()
    for target in ("task_profile", "baseline_commit", "baseline_entrypoint", "system_safety_policy"):
        receipt = apply_contract_mutation(
            run_dir,
            user_input=user_input,
            proposal=ContractMutationProposal(
                base_draft_sha256=None,
                full_turn_mutation_evidence=user_input,
                operations=[_operation(user_input, "set", target, "unsafe", user_input)],
            ),
        )
        assert receipt.status == "rejected"
        assert receipt.reason == "unsupported_target"
    assert load_contract_draft(run_dir) is None


def test_user_forbidden_scope_is_distinct_from_system_safety_policy(tmp_path: Path):
    run_dir = tmp_path / "run_user_boundary"
    run_dir.mkdir()
    user_input = "不要修改评估脚本，也不要使用低秩微调。"

    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256=None,
            full_turn_mutation_evidence=user_input,
            operations=[_operation(
                user_input,
                "set",
                "forbidden_change_scope",
                ["evaluation_script", "low_rank_finetuning"],
                user_input,
            )],
        ),
    )

    assert receipt.status == "applied"
    durable = load_contract_draft(run_dir)
    assert durable is not None
    assert durable.forbidden_change_scope == ["evaluation_script", "low_rank_finetuning"]
    assert durable.system_safety_policy != durable.forbidden_change_scope
    assert "change_metric_definition" in durable.system_safety_policy


def test_missing_run_is_not_recreated(tmp_path: Path):
    run_dir = tmp_path / "deleted_run"
    user_input = "目标是新目标。"

    try:
        apply_contract_mutation(
            run_dir,
            user_input=user_input,
            proposal=ContractMutationProposal(
                base_draft_sha256=None,
                full_turn_mutation_evidence=user_input,
                operations=[_operation(user_input, "set", "research_goal", "新目标", "新目标")],
            ),
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing run must not be recreated")
    assert not run_dir.exists()


def test_synonymous_plan_only_requests_have_equivalent_mutation_structure(tmp_path: Path):
    turns = [
        "先别动代码，只评估这个方案的可行性。",
        "当前只做设计，判断这个方案是否可行。",
        "保持只读，先分析这个方案能不能成立。",
        "我想先评估可行性，不要执行任何修改。",
        "先给研究设计，不运行代码或实验。",
    ]
    changed_structures: list[list[str]] = []

    for index, user_input in enumerate(turns):
        run_dir = tmp_path / f"run_synonym_{index}"
        run_dir.mkdir()
        receipt = apply_contract_mutation(
            run_dir,
            user_input=user_input,
            proposal=ContractMutationProposal(
                base_draft_sha256=None,
                full_turn_mutation_evidence=user_input,
                operations=[_operation(
                    user_input,
                    "set",
                    "research_goal",
                    "评估方案可行性",
                    user_input,
                )],
            ),
        )
        assert receipt.status == "applied"
        changed_structures.append(receipt.changed_fields)

    assert changed_structures == [["research_goal"]] * len(turns)


def test_entity_renaming_preserves_operation_targets_without_value_leakage(tmp_path: Path):
    variants = [
        ("Model-X", "Metric-Z"),
        ("Method-Q", "Measure-R"),
        ("System-A", "Criterion-B"),
    ]
    structures: list[list[str]] = []

    for index, (research_object, metric) in enumerate(variants):
        user_input = f"复现 {research_object}，只报告 {metric}。"
        run_dir = tmp_path / f"run_entity_{index}"
        run_dir.mkdir()
        receipt = apply_contract_mutation(
            run_dir,
            user_input=user_input,
            proposal=ContractMutationProposal(
                base_draft_sha256=None,
                full_turn_mutation_evidence=user_input,
                operations=[
                    _operation(user_input, "set", "research_object", research_object, research_object),
                    _operation(user_input, "set", "primary_metrics", [metric], metric),
                ],
            ),
        )
        assert receipt.status == "applied"
        structures.append(receipt.changed_fields)
        durable = load_contract_draft(run_dir)
        assert durable is not None
        assert durable.research_object == research_object
        assert durable.primary_metrics == [metric]

    assert structures == [["research_object", "primary_metrics"]] * len(variants)


def test_negated_metric_replaces_only_the_explicit_metric_field(tmp_path: Path):
    run_dir = tmp_path / "run_negated_metric"
    original = ResearchIntentContract(
        run_id=run_dir.name,
        research_goal="优化运行资源",
        primary_metrics=["inference_latency"],
        success_criteria="记录基线",
    )
    save_contract_draft(run_dir, original)
    user_input = "我不关注速度，目标是降低峰值显存。"

    receipt = apply_contract_mutation(
        run_dir,
        user_input=user_input,
        proposal=ContractMutationProposal(
            base_draft_sha256=confirmation_draft_sha256(original),
            full_turn_mutation_evidence=user_input,
            operations=[_operation(
                user_input,
                "replace",
                "primary_metrics",
                ["peak_vram"],
                user_input,
            )],
        ),
    )

    assert receipt.status == "applied"
    assert receipt.changed_fields == ["primary_metrics"]
    durable = load_contract_draft(run_dir)
    assert durable is not None
    assert durable.primary_metrics == ["peak_vram"]
    assert durable.research_goal == original.research_goal
    assert durable.success_criteria == original.success_criteria
