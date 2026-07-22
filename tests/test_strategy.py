from pathlib import Path

import pytest

from autoad_researcher.experiment.convergence import ConvergenceAlert
from autoad_researcher.experiment.strategy import SkillDescriptor, StrategyContext, StrategySelector


def _skill(run_dir: Path, skill_id: str, **updates) -> SkillDescriptor:
    directory = run_dir / "skills" / skill_id
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
    values = {
        "skill_id": skill_id,
        "directory_ref": f"skills/{skill_id}",
        "task_types": ["anomaly_detection"],
        "scope": "global",
        "effect_lifetime": "attempt",
    }
    values.update(updates)
    return SkillDescriptor.model_validate(values)


def _alert(**updates) -> ConvergenceAlert:
    values = {
        "session_id": "session_000001",
        "level": "paradigm_shift",
        "consecutive_no_progress": 10,
        "duplicate_rate": 0.4,
        "suggested_skills": ["diversify-axes", "revisit-pruned-lessons"],
        "created_at": "2026-07-18T00:00:00+00:00",
    }
    values.update(updates)
    return ConvergenceAlert.model_validate(values)


def test_selector_filters_and_ranks_without_choosing_for_coordinator(tmp_path: Path):
    diversify = _skill(tmp_path, "diversify-axes")
    revisit = _skill(tmp_path, "revisit-pruned-lessons", requires_approval=True)
    disabled = _skill(tmp_path, "unsafe-pivot", affects_safety_constraints=True)
    selection = StrategySelector().filter_and_rank(
        tmp_path,
        alert=_alert(),
        skills=[revisit, disabled, diversify],
        context=StrategyContext(
            task_type="anomaly_detection",
            approved_skill_ids={"revisit-pruned-lessons"},
        ),
    )
    assert selection.eligible_skill_candidates == ["diversify-axes", "revisit-pruned-lessons"]
    assert selection.eligible_skill_directories == ["skills/diversify-axes", "skills/revisit-pruned-lessons"]
    unsafe = next(item for item in selection.evaluations if item.skill_id == "unsafe-pivot")
    assert not unsafe.eligible
    assert "frozen safety constraints" in unsafe.reasons[0]
    assert (tmp_path / "experiments" / "strategies" / "session_000001" / "selection_events.jsonl").is_file()


def test_selector_rejects_missing_skill_budget_and_repetition(tmp_path: Path):
    missing = SkillDescriptor(
        skill_id="missing",
        directory_ref="skills/missing",
        task_types=["anomaly_detection"],
        scope="axis",
        effect_lifetime="session",
    )
    repeated = _skill(tmp_path, "diversify-axes")
    selection = StrategySelector().filter_and_rank(
        tmp_path,
        alert=_alert(),
        skills=[missing, repeated],
        context=StrategyContext(
            task_type="anomaly_detection",
            repeated_skill_ids={"diversify-axes"},
        ),
    )
    assert selection.eligible_skill_candidates == []
    reasons = {item.skill_id: item.reasons for item in selection.evaluations}
    assert "skill directory or SKILL.md is missing" in reasons["missing"]
    assert "skill was already repeated consecutively" in reasons["diversify-axes"]


def test_resolve_selected_skill_directories_validates_coordinator_choice(tmp_path: Path):
    descriptor = _skill(tmp_path, "diversify-axes")
    selector = StrategySelector()
    selection = selector.filter_and_rank(
        tmp_path,
        alert=_alert(),
        skills=[descriptor],
        context=StrategyContext(task_type="anomaly_detection"),
    )
    resolved = selector.resolve_selected_skill_directories(
        tmp_path,
        selection=selection,
        selected_skill_ids=["diversify-axes"],
    )
    assert resolved == [str(tmp_path / "skills" / "diversify-axes")]
    with pytest.raises(ValueError, match="ineligible"):
        selector.resolve_selected_skill_directories(tmp_path, selection=selection, selected_skill_ids=["not-listed"])
