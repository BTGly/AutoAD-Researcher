"""测试 clarification schemas。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas import (
    ArtifactReference,
    ClarificationQuestion,
    ClarifiedTask,
    KnownFact,
)


class TestKnownFact:
    def test_minimal_valid(self):
        f = KnownFact(
            fact_id="user_baseline",
            category="baseline",
            statement="用户指定 baseline=PatchCore",
            references=[ArtifactReference(artifact="input_task.yaml", locator="baseline")],
        )
        assert f.fact_id == "user_baseline"

    def test_minimum_one_reference(self):
        with pytest.raises(ValidationError):
            KnownFact(
                fact_id="x",
                category="baseline",
                statement="x",
                references=[],
            )


class TestClarificationQuestion:
    def test_free_text_no_options_ok(self):
        q = ClarificationQuestion(
            question_id="q1",
            missing_item_id="m1",
            question="你的 baseline 是什么？",
            why_needed="需要 baseline",
            answer_type="free_text",
        )
        assert q.options == []

    def test_choice_question_requires_options(self):
        with pytest.raises(ValidationError, match="options"):
            ClarificationQuestion(
                question_id="q1",
                missing_item_id="m1",
                question="选一个 baseline",
                why_needed="需要 baseline",
                answer_type="single_choice",
            )


class TestClarifiedTask:
    def test_minimal_ready(self):
        ct = ClarifiedTask(
            run_id="run_demo",
            status="ready",
            original_request="把论文迁移到异常检测",
        )
        assert ct.questions == []

    def test_duplicate_fact_id_rejected(self):
        with pytest.raises(ValidationError, match="duplicate fact_id"):
            ClarifiedTask(
                run_id="run_demo",
                status="ready",
                original_request="x",
                known_facts=[
                    KnownFact(fact_id="dup", category="baseline", statement="a",
                              references=[ArtifactReference(artifact="input_task.yaml", locator="x")]),
                    KnownFact(fact_id="dup", category="dataset", statement="b",
                              references=[ArtifactReference(artifact="input_task.yaml", locator="x")]),
                ],
            )

    def test_duplicate_missing_item_id_rejected(self):
        with pytest.raises(ValidationError, match="duplicate missing"):
            ClarifiedTask(
                run_id="run_demo",
                status="has_nonblocking_questions",
                original_request="x",
                missing_information=[
                    __import__("autoad_researcher.schemas", fromlist=["MissingInformation"]).MissingInformation(
                        item_id="dup", category="baseline", field="baseline", reason="x"),
                    __import__("autoad_researcher.schemas", fromlist=["MissingInformation"]).MissingInformation(
                        item_id="dup", category="dataset", field="dataset", reason="x"),
                ],
            )

    def test_question_references_unknown_missing(self):
        from autoad_researcher.schemas import MissingInformation
        with pytest.raises(ValidationError, match="unknown missing"):
            ClarifiedTask(
                run_id="run_demo",
                status="has_nonblocking_questions",
                original_request="x",
                missing_information=[
                    MissingInformation(item_id="m1", category="baseline", field="baseline", reason="x"),
                ],
                questions=[
                    ClarificationQuestion(
                        question_id="q1", missing_item_id="nonexistent",
                        question="?", why_needed="?", answer_type="free_text",
                    ),
                ],
            )

    def test_blocking_forces_needs_blocking_input(self):
        from autoad_researcher.schemas import MissingInformation
        with pytest.raises(ValidationError, match="needs_blocking_input"):
            ClarifiedTask(
                run_id="run_demo",
                status="has_nonblocking_questions",
                original_request="x",
                missing_information=[
                    MissingInformation(
                        item_id="m1", category="domain", field="target_domain",
                        reason="x", blocking=True,
                    ),
                ],
            )

    def test_no_questions_requires_ready(self):
        with pytest.raises(ValidationError, match="ready"):
            ClarifiedTask(
                run_id="run_demo",
                status="has_nonblocking_questions",
                original_request="x",
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ClarifiedTask(
                run_id="run_demo",
                status="ready",
                original_request="x",
                extra_field="no",  # type: ignore[call-arg]
            )


class TestArtifactReference:
    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            ArtifactReference(
                artifact="input_task.yaml",
                locator="baseline",
                extra_field="no",  # type: ignore[call-arg]
            )
