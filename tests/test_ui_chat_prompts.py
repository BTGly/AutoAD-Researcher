"""Tests for Phase 2E fix: research chat prompt alignment with v0.5 intent alignment."""

import io
import json
from pathlib import Path

import pytest

from autoad_researcher.ui.chat_prompts import INTENT_CLARIFICATION_PROMPT


_MUST_OUTPUT_SECTION = "## 你必须优先输出"


def _must_output_text() -> str:
    """Extract the 'must output' section from the prompt for targeted checks."""
    idx = INTENT_CLARIFICATION_PROMPT.find(_MUST_OUTPUT_SECTION)
    if idx == -1:
        return ""
    rest = INTENT_CLARIFICATION_PROMPT[idx:]
    # Stop at the next ## section
    next_section = rest[3:].find("\n## ")
    if next_section != -1:
        rest = rest[:3 + next_section]
    return rest


def _make_upload(name: str, content: bytes = b"fake pdf content"):
    uploaded = io.BytesIO(content)
    uploaded.name = name
    uploaded.getvalue = lambda: content
    return uploaded


class TestIntentClarificationPrompt:
    """Verify the prompt no longer hardcodes internal benchmark defaults
    or requires execution-layer output fields in the must-output section."""

    def test_no_hardcoded_benchmark_defaults_in_output(self):
        output = _must_output_text()
        assert "MVTec AD（bottle" not in output
        assert "wideresnet50" not in output
        assert "instance_auroc" not in output
        assert "full_pixel_auroc" not in output

    def test_no_execution_layer_output_requirements_in_output(self):
        output = _must_output_text()
        assert "**允许修改**" not in output
        assert "**禁止修改**" not in output
        assert "验收标准" not in output

    def test_has_propose_first_guidance(self):
        assert "Propose first" in INTENT_CLARIFICATION_PROMPT

    def test_has_goal_vs_approach_separation(self):
        prohibited = ["method", "algorithm", "hyperparameters", "patch hook", "variant choice"]
        prompt_lower = INTENT_CLARIFICATION_PROMPT.lower()
        for word in prohibited:
            assert word in prompt_lower, f"Prompt must forbid '{word}'"

    def test_has_safe_confirmation_wording(self):
        assert "不代表允许修改代码" in INTENT_CLARIFICATION_PROMPT
        assert "不代表" in INTENT_CLARIFICATION_PROMPT

    def test_no_hardcoded_benchmark_sentence_anywhere(self):
        """Old hardcoded patterns must not return anywhere in the prompt."""
        assert "当前项目内部 benchmark 基于" not in INTENT_CLARIFICATION_PROMPT
        assert "数据集：MVTec AD" not in INTENT_CLARIFICATION_PROMPT
        assert "基线模型：PatchCore" not in INTENT_CLARIFICATION_PROMPT
        assert "评估指标：instance_auroc" not in INTENT_CLARIFICATION_PROMPT

    def test_prompt_distinguishes_references_uploads_and_parsed_artifacts(self):
        assert "Candidate References" in INTENT_CLARIFICATION_PROMPT
        assert "uploaded_not_parsed" in INTENT_CLARIFICATION_PROMPT
        assert "Parsed Paper Evidence" in INTENT_CLARIFICATION_PROMPT
        assert "未看到" in INTENT_CLARIFICATION_PROMPT

    def test_prompt_marks_reproduction_transfer_as_ambiguous(self):
        assert "复现论文，看看能不能用到我的项目里" in INTENT_CLARIFICATION_PROMPT
        assert "完整复现 vs 方法迁移 / 可用性验证" in INTENT_CLARIFICATION_PROMPT


class TestBuildResearchChatMessages:
    """Verify that intent_clarification messages include WhatWeKnow."""

    def test_intent_clarification_includes_what_we_know(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="我想复现论文",
            context_data={},
        )

        assert len(messages) >= 3

        www_msgs = [m for m in messages if "已有 artifact 探测结果" in m["content"]]
        assert len(www_msgs) == 1
        assert "missing_fields" in www_msgs[0]["content"]

    def test_non_intent_mode_skips_what_we_know(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="run_explanation",
            user_input="现在到哪了",
            context_data={},
        )

        www_msgs = [m for m in messages if "已有 artifact 探测结果" in m["content"]]
        assert len(www_msgs) == 0

    def test_missing_run_dir_does_not_crash(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "nonexistent"

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="测试",
            context_data={},
        )

        assert len(messages) >= 3
        www_msgs = [m for m in messages if "已有 artifact 探测结果" in m["content"]]
        assert len(www_msgs) == 1

    def test_intent_clarification_includes_structured_evidence_context(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="基于论文 artifacts 回答",
            context_data={},
        )

        evidence_msgs = [m for m in messages if "ResearchChatEvidenceContext" in m["content"]]
        assert len(evidence_msgs) == 1
        assert "candidate_references" in evidence_msgs[0]["content"]
        assert "uploaded_unparsed_sources" in evidence_msgs[0]["content"]
        assert "parsed_paper_evidence" in evidence_msgs[0]["content"]
        assert "Candidate References 不是 Known Facts" in evidence_msgs[0]["content"]


class TestTranscriptTail:
    """Verify transcript_tail is injected and current user_input does not repeat."""

    def test_transcript_tail_appears_in_messages(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        tail = [
            {"role": "user", "content": "我想复现 SimpleNet 论文"},
            {"role": "assistant", "content": "好的，候选理解是方法迁移。"},
        ]

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="不是完整复现，是迁移",
            context_data={},
            transcript_tail=tail,
        )

        assert "SimpleNet" in str(messages)
        assert "候选理解是方法迁移" in str(messages)

    def test_current_user_input_not_duplicated(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        tail = [
            {"role": "user", "content": "复现 SimpleNet"},
        ]

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="不是完整复现，是迁移",
            context_data={},
            transcript_tail=tail,
        )

        user_msgs = [m for m in messages if m["role"] == "user"]
        contents = [m["content"] for m in user_msgs]
        assert contents.count("不是完整复现，是迁移") == 1

    def test_transcript_none_does_not_crash(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="test",
            context_data={},
            transcript_tail=None,
        )
        assert len(messages) >= 3


class TestSourceReferencesInjection:
    """Verify intent_clarification injects SourceReferences from registry."""

    def test_intent_clarification_includes_source_references(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        (run_dir / "sources").mkdir()
        registry = {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_001",
                    "kind": "paper_pdf",
                    "user_label": "SimpleNet.pdf",
                    "status": "uploaded_not_parsed",
                    "stored_path": "sources/src_001/SimpleNet.pdf",
                }
            ],
        }
        (run_dir / "sources" / "source_references.json").write_text(
            json.dumps(registry, indent=2)
        )

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="我想复现论文",
            context_data={},
        )

        src_msgs = [m for m in messages if "SourceReferences" in m["content"]]
        assert len(src_msgs) == 1
        assert "SimpleNet.pdf" in src_msgs[0]["content"]
        assert "uploaded_not_parsed" in src_msgs[0]["content"]

    def test_run_explanation_skips_source_references(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="run_explanation",
            user_input="现在到哪了",
            context_data={},
        )

        src_msgs = [m for m in messages if "SourceReferences" in m["content"]]
        assert len(src_msgs) == 0


class TestPdfParseRouting:
    """Verify PDF parse requests are routed before normal LLM chat."""

    def test_detects_natural_language_parse_intents(self):
        from autoad_researcher.ui.research_chat import detect_parse_intent

        assert detect_parse_intent("读一下 sources/src_001/SimpleNet.pdf")
        assert detect_parse_intent("读一下论文pdf")
        assert detect_parse_intent("读一下这个 PDF")
        assert detect_parse_intent("读论文呀")
        assert detect_parse_intent("你再提取一次")
        assert detect_parse_intent("解析刚刚上传的论文")
        assert detect_parse_intent("看一下上传的材料")
        assert not detect_parse_intent("我想提升异常检测指标")

    def test_single_pending_pdf_auto_selects_for_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

        action = build_pdf_parse_action(run_dir, "读一下论文pdf")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_single_pending_pdf_short_read_command_auto_selects_for_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

        action = build_pdf_parse_action(run_dir, "读一下")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_single_pending_pdf_colloquial_read_paper_auto_selects_for_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

        action = build_pdf_parse_action(run_dir, "读论文呀")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_single_pending_pdf_short_confirmation_after_parse_prompt_routes_to_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

        action = build_pdf_parse_action(run_dir, "对啊")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_short_confirmation_without_pending_pdf_stays_chat(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        action = build_pdf_parse_action(run_dir, "对啊")

        assert action["action"] == "chat"

    def test_recent_uploaded_pdf_takes_parse_request_without_path(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action, save_chat_attachments

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        sources = save_chat_attachments(run_dir, [_make_upload("SimpleNet.pdf")])

        action = build_pdf_parse_action(run_dir, "读一下这个论文", recent_sources=sources)

        assert action["action"] == "parse"
        assert action["stored_path"] == sources[0]["stored_path"]

    def test_recent_multiple_uploaded_pdfs_require_choice(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action, save_chat_attachments

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        sources = save_chat_attachments(run_dir, [_make_upload("A.pdf"), _make_upload("B.pdf")])

        action = build_pdf_parse_action(run_dir, "解析这个 PDF", recent_sources=sources)

        assert action["action"] == "choose"
        assert "A.pdf" in action["message"]
        assert "B.pdf" in action["message"]
        assert "sources/" not in action["message"]

    def test_single_pending_pdf_uppercase_pdf_auto_selects(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

        action = build_pdf_parse_action(run_dir, "读一下这个 PDF")

        assert action["action"] == "parse"

    def test_multiple_pending_pdfs_requires_user_choice(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        first = save_uploaded_file(run_dir, _make_upload("A.pdf"))
        second = save_uploaded_file(run_dir, _make_upload("B.pdf"))

        action = build_pdf_parse_action(run_dir, "解析刚刚上传的论文")

        assert action["action"] == "choose"
        assert "A.pdf" in action["message"]
        assert "B.pdf" in action["message"]
        assert "sources/" not in action["message"]

    def test_no_pdf_does_not_fall_through_to_llm(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        action = build_pdf_parse_action(run_dir, "读一下论文pdf")

        assert action["action"] == "missing"
        assert "上传 PDF" in action["message"]

    def test_parsed_pdf_does_not_parse_again(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file, update_source_status

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        update_source_status(run_dir, info["source_id"], "parsed")

        action = build_pdf_parse_action(run_dir, "读一下这个 PDF")

        assert action["action"] == "already_parsed"

    def test_force_reparse_single_parsed_pdf_routes_to_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file, update_source_status

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        update_source_status(run_dir, info["source_id"], "parsed")

        action = build_pdf_parse_action(run_dir, "你再提取一次")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_force_reparse_explicit_parsed_pdf_routes_to_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file, update_source_status

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        update_source_status(run_dir, info["source_id"], "parsed")

        action = build_pdf_parse_action(run_dir, f"重新解析 {info['stored_path']}")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_force_reparse_multiple_parsed_pdfs_requires_choice(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file, update_source_status

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        first = save_uploaded_file(run_dir, _make_upload("A.pdf"))
        second = save_uploaded_file(run_dir, _make_upload("B.pdf"))
        update_source_status(run_dir, first["source_id"], "parsed")
        update_source_status(run_dir, second["source_id"], "parsed")

        action = build_pdf_parse_action(run_dir, "再提取一次")

        assert action["action"] == "choose"
        assert "A.pdf" in action["message"]
        assert "B.pdf" in action["message"]
        assert "sources/" not in action["message"]

    def test_parsing_pdf_does_not_start_duplicate(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file, update_source_status

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
        update_source_status(run_dir, info["source_id"], "parsing")

        action = build_pdf_parse_action(run_dir, "解析刚刚上传的论文")

        assert action["action"] == "already_parsing"

    def test_explicit_source_path_still_routes_to_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action
        from autoad_researcher.ui.sources import save_uploaded_file

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

        action = build_pdf_parse_action(run_dir, f"读一下 {info['stored_path']}")

        assert action["action"] == "parse"
        assert action["stored_path"] == info["stored_path"]

    def test_ordinary_chat_does_not_trigger_parse(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_pdf_parse_action

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        action = build_pdf_parse_action(run_dir, "我想做异常检测方法迁移")

        assert action["action"] == "chat"

    def test_prompt_warns_not_to_fake_pdf_reading(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        (run_dir / "sources").mkdir()
        registry = {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_001",
                    "kind": "paper_pdf",
                    "user_label": "SimpleNet.pdf",
                    "status": "uploaded_not_parsed",
                    "stored_path": "sources/src_001/SimpleNet.pdf",
                }
            ],
        }
        (run_dir / "sources" / "source_references.json").write_text(
            json.dumps(registry, indent=2)
        )

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="你能看到论文内容了吗？",
            context_data={},
        )

        assert any("do not claim you have read it" in m["content"] for m in messages)


class TestChatInputAttachments:
    def test_normalize_plain_string_submission(self):
        from autoad_researcher.ui.research_chat import normalize_chat_submission

        text, files = normalize_chat_submission("普通聊天")

        assert text == "普通聊天"
        assert files == []

    def test_normalize_dict_like_submission(self):
        from autoad_researcher.ui.research_chat import normalize_chat_submission

        upload = _make_upload("SimpleNet.pdf")
        text, files = normalize_chat_submission({"text": "读一下这个论文", "files": [upload]})

        assert text == "读一下这个论文"
        assert files == [upload]

    def test_normalize_object_submission(self):
        from autoad_researcher.ui.research_chat import normalize_chat_submission

        class Submission:
            text = "解析这个 PDF"
            files = [_make_upload("SimpleNet.pdf")]

        text, files = normalize_chat_submission(Submission())

        assert text == "解析这个 PDF"
        assert len(files) == 1

    def test_save_chat_attachments_single_file(self, tmp_path):
        from autoad_researcher.ui.research_chat import save_chat_attachments
        from autoad_researcher.ui.sources import load_source_registry

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        sources = save_chat_attachments(run_dir, [_make_upload("SimpleNet.pdf")])

        assert len(sources) == 1
        assert sources[0]["kind"] == "paper_pdf"
        assert (run_dir / sources[0]["stored_path"]).is_file()
        reg = load_source_registry(run_dir)
        assert reg["sources"][0]["source_id"] == sources[0]["source_id"]
        assert reg["sources"][0]["status"] == "uploaded_not_parsed"

    def test_save_chat_attachments_multiple_files(self, tmp_path):
        from autoad_researcher.ui.research_chat import save_chat_attachments
        from autoad_researcher.ui.sources import load_source_registry

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        sources = save_chat_attachments(run_dir, [_make_upload("SimpleNet.pdf"), _make_upload("notes.md")])

        assert [source["kind"] for source in sources] == ["paper_pdf", "markdown"]
        reg = load_source_registry(run_dir)
        assert len(reg["sources"]) == 2

    def test_attachment_added_reply_names_files_without_paths(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_attachment_added_reply, save_chat_attachments

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()
        sources = save_chat_attachments(run_dir, [_make_upload("SimpleNet.pdf")])

        reply = build_attachment_added_reply(sources)

        assert "已添加资料" in reply
        assert "SimpleNet.pdf" in reply
        assert "sources/" not in reply
        assert "读一下这个论文" in reply
