"""Tests for Round 5 schema-bound assistant LLM backend foundation."""

import json

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1
from autoad_researcher.assistant.llm_backend import (
    AssistantTextReplyV1,
    SchemaBoundAssistantBackend,
    SchemaBoundOutputError,
    StaticSchemaJSONClient,
)
from autoad_researcher.assistant.probe import WhatWeKnow
from autoad_researcher.assistant.session import AutoADAssistantSession


def _session() -> AutoADAssistantSession:
    return AutoADAssistantSession(session_id="s1", run_id="run_001", mode="intent_structuring")


def _what() -> WhatWeKnow:
    return WhatWeKnow(
        run_id="run_001",
        has_baseline_contract=True,
        has_paper_artifacts=True,
        baseline_method="PatchCore",
        missing_fields=["category", "metric_direction"],
    )


def _draft_payload(**overrides):
    payload = {
        "run_id": "run_001",
        "draft_id": "draft_001",
        "metric_command": "python eval.py",
        "metric_name": "image_auroc",
        "metric_direction": "maximize",
        "baseline": "PatchCore",
        "ambition": "beat_baseline",
        "scope": "mixed",
        "constraints": ["不改 eval 脚本"],
        "evidence_ids": ["baseline_architecture_contract.json"],
    }
    payload.update(overrides)
    return payload


def test_text_reply_validates_dict_payload_and_builds_prompt_request():
    client = StaticSchemaJSONClient(
        {
            "message": "我先基于已有 artifact 提案，只问 blocking 缺口。",
            "blocking_questions": ["metric_direction 是 maximize 还是 minimize？"],
        }
    )
    backend = SchemaBoundAssistantBackend(client)

    reply = backend.complete_text_reply(session=_session(), what_we_know=_what())

    assert isinstance(reply, AssistantTextReplyV1)
    assert reply.blocking_questions == ["metric_direction 是 maximize 还是 minimize？"]
    request = client.requests[0]
    assert request.prompt_id == "assistant.understanding_intent.v1"
    assert request.output_schema == "AssistantTextReplyV1"
    assert "Do not interrogate. Propose first." in request.system_prompt
    assert request.context["what_we_know"]["baseline_method"] == "PatchCore"


def test_text_reply_validates_json_string_payload():
    client = StaticSchemaJSONClient(json.dumps({"message": "结构化 JSON 字符串也必须被校验。"}))
    backend = SchemaBoundAssistantBackend(client)

    reply = backend.complete_text_reply(session=_session(), what_we_know=_what())

    assert reply.message == "结构化 JSON 字符串也必须被校验。"


def test_text_reply_rejects_extra_fields():
    client = StaticSchemaJSONClient({"message": "ok", "execution_approved": True})
    backend = SchemaBoundAssistantBackend(client)

    with pytest.raises(ValidationError):
        backend.complete_text_reply(session=_session(), what_we_know=_what())


def test_text_reply_rejects_non_json_text():
    client = StaticSchemaJSONClient("not json")
    backend = SchemaBoundAssistantBackend(client)

    with pytest.raises(SchemaBoundOutputError, match="JSON object"):
        backend.complete_text_reply(session=_session(), what_we_know=_what())


def test_research_task_draft_validates_against_v1_schema():
    client = StaticSchemaJSONClient(_draft_payload())
    backend = SchemaBoundAssistantBackend(client)

    draft = backend.complete_research_task_draft(session=_session(), what_we_know=_what())

    assert isinstance(draft, ResearchTaskDraftV1)
    assert draft.confirmation == "draft"
    assert client.requests[0].prompt_id == "assistant.research_task_draft.v1"
    assert client.requests[0].output_schema == "ResearchTaskDraftV1"


def test_research_task_draft_rejects_method_level_fields():
    client = StaticSchemaJSONClient(_draft_payload(method="Cross-Scale Attention"))
    backend = SchemaBoundAssistantBackend(client)

    with pytest.raises(ValidationError):
        backend.complete_research_task_draft(session=_session(), what_we_know=_what())


def test_backend_does_not_mutate_session_or_confirm_task():
    session = _session()
    client = StaticSchemaJSONClient(_draft_payload())
    backend = SchemaBoundAssistantBackend(client)

    backend.complete_research_task_draft(session=session, what_we_know=_what())

    assert session.task.confirmed_ref is None
    assert session.task.ready_for_pipeline is False
    assert session.task.execution_approved is False


def test_complete_with_result_returns_auditable_request_and_output():
    client = StaticSchemaJSONClient({"message": "ok"})
    backend = SchemaBoundAssistantBackend(client)

    parsed, result = backend.complete_with_result(
        session=_session(),
        what_we_know=_what(),
        output_model=AssistantTextReplyV1,
    )

    assert parsed.message == "ok"
    assert result.request.output_schema == "AssistantTextReplyV1"
    assert result.parsed_output["message"] == "ok"
