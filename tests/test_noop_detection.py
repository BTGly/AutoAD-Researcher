"""Unit tests for _check_noop_patch no-op detection logic.

These tests use synthetic JSON data and do NOT depend on run artifacts.
"""

import json
import shutil
from pathlib import Path
from tempfile import mkdtemp

import pytest

from autoad_researcher.pipeline.results_analysis_stage import _check_noop_patch


@pytest.fixture
def handoff_dir():
    tmp = Path(mkdtemp())
    patch_dir = tmp / "patch_applicator"
    patch_dir.mkdir(parents=True)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _write(handoff_dir: Path, patch_diff: str | None = None,
           before: str | None = None, after: str | None = None) -> None:
    variants = [{"workspace_id": "ws_test"}]
    vw = variants[0]
    if patch_diff is not None:
        vw["patch_diff_sha256"] = patch_diff
    if before is not None:
        vw["before_sha256"] = before
    if after is not None:
        vw["after_sha256"] = after
    handoff = {"variant_workspaces": variants}
    (handoff_dir / "patch_applicator" / "patch_runner_handoff.json").write_text(
        json.dumps(handoff), encoding="utf-8",
    )


class TestNoopDetection:
    """Edge cases for _check_noop_patch()."""

    def test_patch_diff_null_is_noop(self, handoff_dir):
        _write(handoff_dir, patch_diff=None, before=None, after=None)
        assert _check_noop_patch(handoff_dir) is True

    def test_patch_diff_empty_is_noop(self, handoff_dir):
        _write(handoff_dir, patch_diff="", before="", after="")
        assert _check_noop_patch(handoff_dir) is True

    def test_patch_diff_zeros_is_noop(self, handoff_dir):
        _write(handoff_dir, patch_diff="0" * 64, before="aa", after="aa")
        assert _check_noop_patch(handoff_dir) is True

    def test_nonzero_diff_with_before_eq_after_is_noop(self, handoff_dir):
        _write(handoff_dir, patch_diff="a" * 64,
               before="bb" * 32, after="bb" * 32)
        assert _check_noop_patch(handoff_dir) is True

    def test_nonzero_diff_with_before_ne_after_is_not_noop(self, handoff_dir):
        """Real patch with different before/after must NOT be flagged as no-op."""
        _write(handoff_dir, patch_diff="a" * 64,
               before="bb" * 32, after="cc" * 32)
        assert _check_noop_patch(handoff_dir) is False

    def test_nonzero_diff_with_missing_before_after_is_not_noop(self, handoff_dir):
        """Real patch with absent before/after fields must NOT be no-op (None==None guard)."""
        _write(handoff_dir, patch_diff="a" * 64,
               before=None, after=None)
        assert _check_noop_patch(handoff_dir) is False

    def test_nonzero_diff_with_before_after_empty_is_noop(self, handoff_dir):
        """Empty before/after strings represent uncommitted workspaces — equal == no-op."""
        _write(handoff_dir, patch_diff="a" * 64, before="", after="")
        assert _check_noop_patch(handoff_dir) is True

    def test_no_handoff_file_returns_false(self):
        empty_dir = Path(mkdtemp())
        try:
            assert _check_noop_patch(empty_dir) is False
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_malformed_json_returns_false(self, handoff_dir):
        (handoff_dir / "patch_applicator" / "patch_runner_handoff.json").write_text(
            "not valid json", encoding="utf-8",
        )
        assert _check_noop_patch(handoff_dir) is False
