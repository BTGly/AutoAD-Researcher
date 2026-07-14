"""Tests for Phase 2E task profile — human-readable task title."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.ui.task_profile import (
    TaskProfile,
    apply_automatic_task_profile,
    archive_task,
    build_automatic_task_profile,
    build_run_id_from_optional_name,
    create_task_profile,
    fallback_task_profile,
    format_task_list_label,
    get_task_display_info,
    get_task_title,
    load_task_archive_state,
    list_all_tasks,
    load_task_profile,
    rename_task_title,
    restore_task,
    safe_load_task_profile,
    save_task_profile,
    slugify_task_name,
    task_profile_needs_automatic_title,
    delete_archived_task,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tmp_run_dir(tmp_path: Path, run_id: str = "run_20260703_1200_a3b2") -> Path:
    d = tmp_path / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _valid_profile(run_dir: Path, **overrides) -> TaskProfile:
    kwargs = {
        "run_id": run_dir.name,
        "task_title": "降低 PatchCore 显存",
        "task_summary": "优化显存占用同时保持 AUROC。",
        "source": "llm_first_user_instruction",
    }
    kwargs.update(overrides)
    return TaskProfile(**kwargs)


# ---------------------------------------------------------------------------
# TaskProfile model
# ---------------------------------------------------------------------------


class TestTaskProfile:
    def test_valid_profile(self):
        p = TaskProfile(
            run_id="run_001",
            task_title="降低 PatchCore 显存",
            task_summary="优化显存。",
            source="llm_first_user_instruction",
        )
        assert p.task_title == "降低 PatchCore 显存"
        assert p.schema_version == 1

    def test_rejects_sk_secret_in_title(self):
        with pytest.raises(ValidationError, match="secret"):
            TaskProfile(
                run_id="run_001",
                task_title="使用 sk-abc123def456 优化",
                task_summary="...",
                source="llm_first_user_instruction",
            )

    def test_rejects_sk_secret_in_summary(self):
        with pytest.raises(ValidationError, match="secret"):
            TaskProfile(
                run_id="run_001",
                task_title="降低显存",
                task_summary="用到 sk-abc123def456 密钥",
                source="llm_first_user_instruction",
            )

    def test_rejects_title_that_is_run_id(self):
        with pytest.raises(ValidationError, match="run_id"):
            TaskProfile(
                run_id="run_20260703_1200_a3b2",
                task_title="run_20260703_1200_a3b2",
                task_summary="...",
                source="llm_first_user_instruction",
            )

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            TaskProfile(
                run_id="run_001",
                task_title="Title",
                task_summary="Summary",
                source="fallback",
                extra_field="oops",
            )

    def test_rejects_empty_title(self):
        with pytest.raises(ValidationError):
            TaskProfile(
                run_id="run_001",
                task_title="",
                task_summary="Summary",
                source="fallback",
            )

    def test_rejects_title_too_long(self):
        with pytest.raises(ValidationError):
            TaskProfile(
                run_id="run_001",
                task_title="这是" + "一个非常长的标题" * 5 + "超过了三十个字符的限制应该报错",
                task_summary="Summary",
                source="fallback",
            )


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_save_and_load_roundtrip(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        p = _valid_profile(run_dir)
        saved = save_task_profile(run_dir, p)
        assert saved.name == "task_profile.json"

        loaded = load_task_profile(run_dir)
        assert loaded is not None
        assert loaded.task_title == p.task_title
        assert loaded.task_summary == p.task_summary
        assert loaded.source == p.source
        assert loaded.run_id == run_dir.name

    def test_load_nonexistent(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        assert load_task_profile(run_dir) is None

    def test_rejects_overwrite(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        save_task_profile(run_dir, _valid_profile(run_dir))
        with pytest.raises(FileExistsError):
            save_task_profile(run_dir, _valid_profile(run_dir))

    def test_safe_load_bad_profile_returns_warning(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_bad_profile")
        profile_path = run_dir / "ui_chat" / "task_profile.json"
        profile_path.parent.mkdir()
        profile_path.write_text("{not json", encoding="utf-8")

        profile, warning = safe_load_task_profile(run_dir)

        assert profile is None
        assert warning == "task_profile_invalid:JSONDecodeError"


class TestTaskNamingHelpers:
    def test_slugify_task_name(self):
        assert slugify_task_name("SimpleNet Migration v1") == "simplenet_migration_v1"
        assert slugify_task_name("  A/B Test  ") == "a_b_test"

    def test_build_run_id_without_name(self):
        now = datetime(2026, 7, 5, 8, 19, tzinfo=timezone.utc)
        rid = build_run_id_from_optional_name(task_name=None, now=now)
        assert rid.startswith("run_20260705_0819_")

    def test_build_run_id_with_name_uses_slug_and_time(self):
        now = datetime(2026, 7, 5, 8, 19, tzinfo=timezone.utc)
        rid = build_run_id_from_optional_name(task_name="SimpleNet Migration v1", now=now)
        assert rid.startswith("simplenet_migration_v1_0819_")

    def test_create_task_profile_writes_ui_profile(self, tmp_path):
        run_id = "simplenet_migration_v1_0819_abcd"
        run_dir = tmp_path / run_id
        created_at = datetime(2026, 7, 5, 8, 19, tzinfo=timezone.utc)

        profile = create_task_profile(
            run_dir=run_dir,
            run_id=run_id,
            task_title="SimpleNet Migration v1",
            created_at=created_at,
        )

        assert profile.task_title == "SimpleNet Migration v1"
        assert profile.run_id == run_id
        assert profile.source == "ui"
        assert profile.updated_at == created_at
        assert load_task_profile(run_dir) == profile

    def test_create_task_profile_empty_title_uses_fallback_title(self, tmp_path):
        run_id = "run_20260705_0819_abcd"
        run_dir = tmp_path / run_id
        profile = create_task_profile(
            run_dir=run_dir,
            run_id=run_id,
            task_title=None,
            created_at=datetime(2026, 7, 5, 8, 19, tzinfo=timezone.utc),
        )

        assert profile.task_title == "未命名研究任务"

    def test_rename_task_title_does_not_change_run_id_or_dir(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_keep_id")
        create_task_profile(
            run_dir=run_dir,
            run_id=run_dir.name,
            task_title="Old Title",
            created_at=datetime(2026, 7, 5, 8, 19, tzinfo=timezone.utc),
        )
        old_path = run_dir

        updated = rename_task_title(
            run_dir=run_dir,
            new_title="New Title",
            updated_at=datetime(2026, 7, 5, 9, 0, tzinfo=timezone.utc),
        )

        assert updated.task_title == "New Title"
        assert updated.run_id == "run_keep_id"
        assert run_dir == old_path
        assert run_dir.is_dir()

    def test_rename_missing_profile_creates_profile_for_existing_run(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_legacy")
        updated = rename_task_title(
            run_dir=run_dir,
            new_title="Legacy Renamed",
            updated_at=datetime(2026, 7, 5, 9, 0, tzinfo=timezone.utc),
        )
        assert updated.run_id == "run_legacy"
        assert updated.task_title == "Legacy Renamed"
        assert load_task_profile(run_dir).task_title == "Legacy Renamed"


class TestTaskListing:
    def test_list_all_tasks_profile_and_fallback(self, tmp_path):
        first = _tmp_run_dir(tmp_path, "run_first")
        second = _tmp_run_dir(tmp_path, "run_second")
        create_task_profile(
            run_dir=first,
            run_id=first.name,
            task_title="First Task",
            created_at=datetime(2026, 7, 5, 8, 0, tzinfo=timezone.utc),
        )

        items = list_all_tasks(runs_root=tmp_path)
        by_id = {item.run_id: item for item in items}

        assert by_id["run_first"].task_title == "First Task"
        assert by_id["run_first"].source == "profile"
        assert by_id["run_second"].task_title == "历史研究任务"
        assert by_id["run_second"].source == "fallback"

    def test_list_all_tasks_skips_hidden_and_files(self, tmp_path):
        _tmp_run_dir(tmp_path, "run_visible")
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "not_a_run.txt").write_text("x", encoding="utf-8")

        items = list_all_tasks(runs_root=tmp_path)

        assert [item.run_id for item in items] == ["run_visible"]

    def test_list_all_tasks_bad_profile_falls_back(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_bad")
        profile_path = run_dir / "ui_chat" / "task_profile.json"
        profile_path.parent.mkdir()
        profile_path.write_text("{not json", encoding="utf-8")

        items = list_all_tasks(runs_root=tmp_path)

        assert len(items) == 1
        assert items[0].task_title == "历史研究任务"
        assert items[0].profile_warning is not None

    def test_list_all_tasks_sorts_by_updated_at_desc(self, tmp_path):
        older = _tmp_run_dir(tmp_path, "run_older")
        newer = _tmp_run_dir(tmp_path, "run_newer")
        create_task_profile(
            run_dir=older,
            run_id=older.name,
            task_title="Older",
            created_at=datetime(2026, 7, 5, 8, 0, tzinfo=timezone.utc),
        )
        create_task_profile(
            run_dir=newer,
            run_id=newer.name,
            task_title="Newer",
            created_at=datetime(2026, 7, 5, 9, 0, tzinfo=timezone.utc),
        )

        items = list_all_tasks(runs_root=tmp_path)

        assert [item.run_id for item in items] == ["run_newer", "run_older"]

    def test_format_task_list_label(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_label")
        create_task_profile(
            run_dir=run_dir,
            run_id=run_dir.name,
            task_title="Label Task",
            created_at=datetime(2026, 7, 5, 8, 0, tzinfo=timezone.utc),
        )
        item = list_all_tasks(runs_root=tmp_path)[0]
        assert format_task_list_label(item) == "Label Task (2026-07-05 08:00)"

    def test_archived_task_hidden_by_default(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_archived")
        archive_task(
            run_dir=run_dir,
            archived_at=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
        )

        assert list_all_tasks(runs_root=tmp_path) == []
        items = list_all_tasks(runs_root=tmp_path, include_archived=True)
        assert len(items) == 1
        assert items[0].run_id == "run_archived"
        assert items[0].archived_at == datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc)

    def test_restore_task_removes_archive_marker(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_restore")
        archive_task(
            run_dir=run_dir,
            archived_at=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
        )

        restore_task(run_dir=run_dir)

        assert load_task_archive_state(run_dir) is None
        assert list_all_tasks(runs_root=tmp_path)[0].run_id == "run_restore"

    def test_archived_label_marks_task(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_archived_label")
        archive_task(
            run_dir=run_dir,
            archived_at=datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc),
        )

        item = list_all_tasks(runs_root=tmp_path, include_archived=True)[0]

        assert "已归档" in format_task_list_label(item)

    def test_bad_archive_state_does_not_hide_or_crash(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_bad_archive")
        archive_path = run_dir / "ui_chat" / "task_archive.json"
        archive_path.parent.mkdir()
        archive_path.write_text("{not json", encoding="utf-8")

        items = list_all_tasks(runs_root=tmp_path)

        assert len(items) == 1
        assert items[0].run_id == "run_bad_archive"
        assert "task_archive_invalid" in items[0].profile_warning

    def test_delete_archived_task_removes_directory(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_to_delete")
        (run_dir / "artifact.json").write_text("{}", encoding="utf-8")
        archive_task(
            run_dir=run_dir,
            archived_at=datetime(2026, 7, 5, 11, 0, tzinfo=timezone.utc),
        )

        delete_archived_task(run_dir=run_dir)

        assert not run_dir.exists()
        assert list_all_tasks(runs_root=tmp_path, include_archived=True) == []

    def test_delete_unarchived_task_rejected(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_not_archived")

        with pytest.raises(ValueError, match="archived"):
            delete_archived_task(run_dir=run_dir)

        assert run_dir.is_dir()


# ---------------------------------------------------------------------------
# fallback
# ---------------------------------------------------------------------------


class TestFallback:
    def test_fallback_has_expected_title(self):
        p = fallback_task_profile("run_001")
        assert p.task_title == "未命名研究任务"
        assert p.source == "fallback"
        assert p.run_id == "run_001"

    def test_fallback_is_valid_task_profile(self):
        p = fallback_task_profile("run_001")
        TaskProfile.model_validate(p.model_dump())


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


class TestUIHelpers:
    def test_get_task_title_with_profile(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        save_task_profile(run_dir, _valid_profile(run_dir))
        assert get_task_title(run_dir) == "降低 PatchCore 显存"

    def test_get_task_title_without_profile(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        assert get_task_title(run_dir) == run_dir.name

    def test_get_display_info(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        save_task_profile(run_dir, _valid_profile(run_dir))
        info = get_task_display_info(run_dir)
        assert info["task_title"] == "降低 PatchCore 显存"
        assert info["run_id"] == run_dir.name
        assert str(run_dir) in info["artifact_dir"]
        assert info["task_source"] == "llm_first_user_instruction"

    def test_get_display_info_fallback(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        info = get_task_display_info(run_dir)
        assert info["task_title"] == "历史研究任务"
        assert info["task_source"] == "fallback"
        assert info["task_profile_warning"] is None

    def test_get_display_info_includes_run_id(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path)
        info = get_task_display_info(run_dir)
        assert info["run_id"] == run_dir.name

    def test_get_display_info_bad_profile_falls_back_to_run_id(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_bad_current")
        profile_path = run_dir / "ui_chat" / "task_profile.json"
        profile_path.parent.mkdir()
        profile_path.write_text("{not json", encoding="utf-8")

        info = get_task_display_info(run_dir)

        assert info["task_title"] == "历史研究任务"
        assert info["task_source"] == "fallback"
        assert info["task_profile_warning"] == "task_profile_invalid:JSONDecodeError"

    def test_get_display_info_includes_archive_state(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_archived_info")
        archived_at = datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc)
        archive_task(run_dir=run_dir, archived_at=archived_at)

        info = get_task_display_info(run_dir)

        assert info["archived_at"] == archived_at

    def test_legacy_run_fallback_display_matches_task_picker(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, "run_legacy_consistent")

        item = list_all_tasks(runs_root=tmp_path)[0]
        info = get_task_display_info(run_dir)

        assert item.task_title == "历史研究任务"
        assert info["task_title"] == item.task_title


# ---------------------------------------------------------------------------
# Router and deterministic automatic naming
# ---------------------------------------------------------------------------


class TestAutomaticTaskProfile:
    def test_valid_router_suggestion_has_highest_automatic_priority(self):
        profile = build_automatic_task_profile(
            run_id="run_20260703_1200_a3b2",
            suggested_title="PatchCore MVTec AUROC优化",
            suggested_summary="提升 MVTec AD 的图像级 AUROC。",
            user_intent_summary="模型实验",
            task_profile="empirical_model_research",
            task_profile_evidence="PatchCore",
            contract={"baseline": "PatchCore", "dataset": "MVTec AD"},
        )

        assert profile is not None
        assert profile.source == "router_suggested"
        assert profile.task_title == "PatchCore MVTec AUROC优化"

    def test_invalid_router_title_falls_back_to_contract_projection(self):
        profile = build_automatic_task_profile(
            run_id="run_20260703_1200_a3b2",
            suggested_title="/tmp/private/model",
            suggested_summary="不能采用这个路径标题。",
            user_intent_summary="模型实验",
            task_profile="empirical_model_research",
            task_profile_evidence="PatchCore",
            contract={
                "baseline": "PatchCore",
                "dataset": "MVTec AD",
                "primary_metrics": ["image_level_auroc"],
                "research_goal": "提升 image-level AUROC。",
            },
        )

        assert profile is not None
        assert profile.source == "deterministic_projection"
        assert profile.task_title == "PatchCore MVTec image AUROC优化"

    @pytest.mark.parametrize("title", [
        "使用 sk-abc123def456 优化",
        "run_20260703_1200_a3b2",
        "研究任务",
    ])
    def test_unsafe_or_generic_router_title_does_not_bypass_validation(self, title):
        profile = build_automatic_task_profile(
            run_id="run_20260703_1200_a3b2",
            suggested_title=title,
            suggested_summary="test",
            user_intent_summary=None,
            task_profile="general_research",
            task_profile_evidence=None,
            contract={},
        )

        assert profile is None


# ---------------------------------------------------------------------------
# run_id path unchanged
# ---------------------------------------------------------------------------


class TestRunIdPathUnchanged:
    def test_run_id_path_unchanged_by_save(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, run_id="run_my_custom_id")
        save_task_profile(run_dir, _valid_profile(run_dir))
        assert run_dir.name == "run_my_custom_id"
        assert run_dir.is_dir()

    def test_profile_contains_run_id(self, tmp_path):
        run_dir = _tmp_run_dir(tmp_path, run_id="run_abc123")
        profile = _valid_profile(run_dir, run_id="run_abc123")
        assert profile.run_id == "run_abc123"
        assert "run_abc123" not in profile.task_title


# ---------------------------------------------------------------------------
# legacy import compatibility
# ---------------------------------------------------------------------------


def test_task_profile_implementation_moved_out_of_ui_package():
    import autoad_researcher.task_workspace.task_profile as task_workspace_profile
    import autoad_researcher.ui.task_profile as legacy_profile

    assert legacy_profile.TaskProfile is task_workspace_profile.TaskProfile
    assert legacy_profile.create_task_profile is task_workspace_profile.create_task_profile


def test_router_profile_replaces_ui_placeholder_and_persists_source(tmp_path: Path):
    run_dir = _tmp_run_dir(tmp_path, "run_auto_name")
    created_at = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 7, 13, 10, 1, tzinfo=timezone.utc)
    create_task_profile(
        run_dir=run_dir,
        run_id=run_dir.name,
        task_title=None,
        created_at=created_at,
    )
    generated = TaskProfile(
        run_id=run_dir.name,
        task_title="PatchCore 指标优化",
        task_summary="在 MVTec AD 上提升 PatchCore 的 image-level AUROC。",
        source="router_suggested",
    )

    assert task_profile_needs_automatic_title(run_dir) is True
    updated = apply_automatic_task_profile(
        run_dir=run_dir,
        generated_profile=generated,
        updated_at=updated_at,
    )

    assert updated is not None
    assert updated.task_title == "PatchCore 指标优化"
    assert updated.task_summary == generated.task_summary
    assert updated.source == "router_suggested"
    assert updated.created_at == created_at
    assert updated.updated_at == updated_at
    assert task_profile_needs_automatic_title(run_dir) is False


def test_automatic_profile_cannot_overwrite_manual_title(tmp_path: Path):
    run_dir = _tmp_run_dir(tmp_path, "run_manual_name")
    create_task_profile(
        run_dir=run_dir,
        run_id=run_dir.name,
        task_title=None,
        created_at=datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc),
    )
    manual = rename_task_title(
        run_dir=run_dir,
        new_title="我的手动名称",
        updated_at=datetime(2026, 7, 13, 10, 1, tzinfo=timezone.utc),
    )
    generated = TaskProfile(
        run_id=run_dir.name,
        task_title="模型生成名称",
        task_summary="不应覆盖手动名称。",
        source="router_suggested",
    )

    assert task_profile_needs_automatic_title(run_dir) is False
    assert apply_automatic_task_profile(
        run_dir=run_dir,
        generated_profile=generated,
        updated_at=datetime(2026, 7, 13, 10, 2, tzinfo=timezone.utc),
    ) is None
    assert load_task_profile(run_dir) == manual


def test_router_profile_can_upgrade_deterministic_but_not_reverse(tmp_path: Path):
    run_dir = _tmp_run_dir(tmp_path, "run_priority")
    created_at = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    create_task_profile(
        run_dir=run_dir,
        run_id=run_dir.name,
        task_title=None,
        created_at=created_at,
    )
    deterministic = TaskProfile(
        run_id=run_dir.name,
        task_title="PatchCore性能优化",
        task_summary="确定性投影。",
        source="deterministic_projection",
    )
    assert apply_automatic_task_profile(
        run_dir=run_dir,
        generated_profile=deterministic,
        updated_at=created_at,
    ) is not None
    router = TaskProfile(
        run_id=run_dir.name,
        task_title="PatchCore MVTec AUROC优化",
        task_summary="Router 建议。",
        source="router_suggested",
    )
    upgraded = apply_automatic_task_profile(
        run_dir=run_dir,
        generated_profile=router,
        updated_at=datetime(2026, 7, 13, 10, 1, tzinfo=timezone.utc),
    )
    assert upgraded is not None
    assert upgraded.source == "router_suggested"
    assert apply_automatic_task_profile(
        run_dir=run_dir,
        generated_profile=deterministic,
        updated_at=datetime(2026, 7, 13, 10, 2, tzinfo=timezone.utc),
    ) is None
