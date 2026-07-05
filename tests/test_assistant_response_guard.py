"""Tests for deterministic Research Chat response guard."""

from autoad_researcher.assistant.research_context_builder import (
    CandidateReference,
    ParsedPaperEvidence,
    ResearchChatEvidenceContext,
    UploadedUnparsedSource,
)
from autoad_researcher.assistant.response_guard import guard_research_chat_reply


def test_guard_rewrites_paper_claim_without_parsed_artifact():
    context = ResearchChatEvidenceContext(
        candidate_references=[
            CandidateReference(
                source_id="src_arxiv",
                kind="arxiv_id",
                user_label="2303.15140v2",
                status="user_provided_not_ingested",
            )
        ]
    )

    guarded = guard_research_chat_reply(
        reply="论文提出了一个高斯密度估计模块。",
        user_input="基于论文 artifacts 回答",
        evidence_context=context,
    )

    assert "paper_content_without_parsed_artifact" in guarded.violations
    assert "artifact_answer_without_parsed_artifact" in guarded.violations
    assert "尚未解析对应 PDF" in guarded.reply


def test_guard_allows_paper_claim_when_parsed_evidence_exists():
    context = ResearchChatEvidenceContext(
        parsed_paper_evidence=[
            ParsedPaperEvidence(
                artifact_refs=["paper/artifacts/paper_summary.json"],
                paper_methods=["coreset memory bank"],
            )
        ],
        has_parsed_paper_evidence=True,
    )

    guarded = guard_research_chat_reply(
        reply="论文提出了 coreset memory bank。",
        user_input="基于论文 artifacts 回答",
        evidence_context=context,
    )

    assert guarded.reply == "论文提出了 coreset memory bank。"
    assert guarded.violations == []


def test_guard_rewrites_repo_claim_without_repo_evidence():
    context = ResearchChatEvidenceContext()

    guarded = guard_research_chat_reply(
        reply="仓库中实现了 PatchCore 的完整训练入口。",
        user_input="看一下 repo",
        evidence_context=context,
    )

    assert guarded.violations == ["repo_content_without_repo_evidence"]
    assert "尚未完成 repository intelligence" in guarded.reply


def test_guard_rewrites_execution_promise_without_approval():
    context = ResearchChatEvidenceContext(has_parsed_paper_evidence=True)

    guarded = guard_research_chat_reply(
        reply="确认后我开始执行并运行实验。",
        user_input="直接跑实验",
        evidence_context=context,
        execution_approved=False,
    )

    assert guarded.violations == ["execution_promise_without_approval"]
    assert "任务确认不等于代码修改批准" in guarded.reply


def test_guard_prefers_unparsed_file_message_when_pdf_uploaded():
    context = ResearchChatEvidenceContext(
        uploaded_unparsed_sources=[
            UploadedUnparsedSource(
                source_id="src_pdf",
                kind="paper_pdf",
                user_label="SimpleNet.pdf",
                status="uploaded_not_parsed",
                stored_path="sources/src_pdf/SimpleNet.pdf",
            )
        ]
    )

    guarded = guard_research_chat_reply(
        reply="论文提出了新的判别器。",
        user_input="基于论文 artifacts 回答",
        evidence_context=context,
    )

    assert "文件或引用已提供" in guarded.reply or "资料已进入当前任务" in guarded.reply
    assert "不能基于论文正文判断" in guarded.reply
