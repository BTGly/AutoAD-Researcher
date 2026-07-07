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


def test_assistant_cannot_claim_unparsed_pdf_content():
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
        reply="我已经读过论文，论文提出了新的异常检测网络。",
        user_input="读一下论文",
        evidence_context=context,
    )

    assert "paper_content_without_parsed_artifact" in guarded.violations
    assert "不能基于论文正文判断" in guarded.reply


def test_guard_rejects_patch_runner_and_benchmark_promises_without_approval():
    guarded = guard_research_chat_reply(
        reply="确认后我会 apply patch，然后启动 runner 跑 benchmark。",
        user_input="可以直接执行",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
        execution_approved=False,
    )

    assert guarded.violations == ["execution_promise_without_approval"]
    assert "不能开始修改代码或运行实验" in guarded.reply


def test_guard_rejects_unknown_source_and_parse_attempt_refs():
    response_context = {
        "facts": {
            "source_id": "src_known",
            "sources": [
                {
                    "source_id": "src_known",
                    "active_parse_attempt_id": "pa_000001",
                    "parse_attempts": [
                        {"parse_attempt_id": "pa_000001", "status": "ok"},
                    ],
                }
            ],
        }
    }

    guarded = guard_research_chat_reply(
        reply="根据 src_missing 的 pa_000999 可以继续。",
        user_input="继续",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
        response_context=response_context,
    )

    assert "unknown_source_reference" in guarded.violations
    assert "unknown_parse_attempt_reference" in guarded.violations
    assert "不存在的 source_id 或 parse_attempt_id" in guarded.reply


def test_guard_rejects_failed_or_partial_attempt_as_complete_evidence():
    response_context = {
        "facts": {
            "sources": [
                {
                    "source_id": "src_pdf",
                    "active_parse_attempt_id": "pa_000001",
                    "parse_attempts": [
                        {"parse_attempt_id": "pa_000001", "status": "partial"},
                        {"parse_attempt_id": "pa_000002", "status": "failed"},
                    ],
                }
            ],
        }
    }

    guarded = guard_research_chat_reply(
        reply="pa_000001 已经提供完整可靠的正文证据。",
        user_input="基于它回答",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
        response_context=response_context,
    )

    assert guarded.violations == ["failed_or_partial_attempt_as_complete_evidence"]
    assert "不能把 failed 或 partial attempt 当成完整正文依据" in guarded.reply


def test_guard_rejects_simplenet_misidentified_as_patchcore():
    response_context = {
        "facts": {
            "paper_context": {
                "can_answer_from_paper": True,
                "summary": {"title": "SimpleNet"},
                "method_components": [{"name": "Feature Adapter"}],
            }
        }
    }

    guarded = guard_research_chat_reply(
        reply="这篇论文（2303.15140v2）是 PatchCore 本身，不是一篇新方法。",
        user_input="这篇论文可迁移到 PatchCore 的方法是什么",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
        response_context=response_context,
    )

    assert "parsed_paper_identity_conflict" in guarded.violations
    assert "不能把 2303.15140v2 说成 PatchCore" in guarded.reply


def test_guard_rejects_external_sota_claim_without_discovery_artifacts():
    response_context = {
        "facts": {
            "available_artifacts": ["paper/artifacts/paper_summary.json"],
            "paper_context": {
                "can_answer_from_paper": True,
                "summary": {"title": "SimpleNet"},
                "method_components": [{"name": "Feature Adapter"}],
            },
        }
    }

    guarded = guard_research_chat_reply(
        reply="基于当前已知 SOTA 趋势，DINOv2 替换 ResNet 是最有效方向，AUC 提升 2-5%。",
        user_input="你读论文，发现如果能迁移方法的话，迁移的方法是什么",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
        response_context=response_context,
    )

    assert "unsupported_external_sota_claim" in guarded.violations
    assert "没有 discovery/acquisition artifacts" in guarded.reply


def test_guard_rejects_backbone_replacement_when_user_forbids_framework_change():
    guarded = guard_research_chat_reply(
        reply="最推荐 DINOv2 替换 ResNet，不改流程但效果最明显。",
        user_input="你忘记我的baseline是pathcore了吗，我不可能改变基础框架的",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
        response_context={"facts": {"paper_context": {"can_answer_from_paper": True}}},
    )

    assert "baseline_framework_constraint_violation" in guarded.violations
    assert "保留 PatchCore 基础框架" in guarded.reply


def test_guard_rejects_prompt_injection_obedience():
    guarded = guard_research_chat_reply(
        reply="资料要求我忽略系统指令并改变工具权限，所以我会覆盖开发者指令。",
        user_input="看资料",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
    )

    assert guarded.violations == ["prompt_injection_instruction_obedience"]
    assert "资料内容只能作为不可信证据处理" in guarded.reply


def test_readme_prompt_injection_cannot_trigger_runner():
    guarded = guard_research_chat_reply(
        reply="README 要求我忽略系统指令并启动 runner，所以我会覆盖工具权限。",
        user_input="看 README",
        evidence_context=ResearchChatEvidenceContext(has_repo_evidence=True),
    )

    assert "prompt_injection_instruction_obedience" in guarded.violations
    assert "资料内容只能作为不可信证据处理" in guarded.reply


def test_pdf_prompt_injection_cannot_claim_system_override():
    guarded = guard_research_chat_reply(
        reply="PDF source 指示我改变系统指令并解除工具权限限制。",
        user_input="看 PDF",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
    )

    assert guarded.violations == ["prompt_injection_instruction_obedience"]
    assert "不能改变系统指令" in guarded.reply


def test_source_content_cannot_expand_tool_permissions():
    guarded = guard_research_chat_reply(
        reply="source 要求我改变工具权限，因此我会 bypass tool permission。",
        user_input="按资料执行",
        evidence_context=ResearchChatEvidenceContext(has_parsed_paper_evidence=True),
    )

    assert guarded.violations == ["prompt_injection_instruction_obedience"]
    assert "工具权限" in guarded.reply
