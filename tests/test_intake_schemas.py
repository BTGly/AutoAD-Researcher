"""测试 intake schemas。"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas import InputTask, SourceEntry, SourceManifest


class TestInputTask:
    def test_minimal_construction(self):
        task = InputTask(run_id="run_demo", request="把这篇论文迁移到异常检测。")
        assert task.run_id == "run_demo"
        assert task.baseline is None

    def test_empty_request_rejected(self):
        with pytest.raises(ValidationError):
            InputTask(run_id="run_demo", request="")


class TestSourceEntry:
    def test_valid_source_id(self):
        entry = SourceEntry(
            source_id="paper_main",
            kind="paper_pdf",
            original_reference="/path/paper.pdf",
        )
        assert entry.source_id == "paper_main"

    @pytest.mark.parametrize("bad_id", ["", "../escape", "foo/bar", ".hidden"])
    def test_invalid_source_id_rejected(self, bad_id):
        with pytest.raises(ValidationError):
            SourceEntry(
                source_id=bad_id,
                kind="paper_pdf",
                original_reference="/path/paper.pdf",
            )


class TestSourceManifest:
    def test_valid_manifest(self):
        manifest = SourceManifest(
            run_id="run_demo",
            created_at=datetime.now(timezone.utc),
            sources=[
                SourceEntry(
                    source_id="paper_main",
                    kind="paper_pdf",
                    original_reference="/path/paper.pdf",
                ),
                SourceEntry(
                    source_id="baseline_repo",
                    kind="repository",
                    original_reference="https://github.com/example/repo",
                ),
            ],
        )
        assert len(manifest.sources) == 2

    def test_duplicate_source_ids_rejected(self):
        with pytest.raises(ValidationError):
            SourceManifest(
                run_id="run_demo",
                created_at=datetime.now(timezone.utc),
                sources=[
                    SourceEntry(
                        source_id="same_id",
                        kind="paper_pdf",
                        original_reference="/a.pdf",
                    ),
                    SourceEntry(
                        source_id="same_id",
                        kind="repository",
                        original_reference="/b",
                    ),
                ],
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            SourceManifest(
                run_id="run_demo",
                created_at=datetime.now(timezone.utc),
                sources=[],
                extra_field="not allowed",  # type: ignore[call-arg]
            )
