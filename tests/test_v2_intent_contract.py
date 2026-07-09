from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_DRAFT_FILE,
    CONTRACT_FILE,
    DEFAULT_FORBIDDEN_CHANGE_SCOPE,
    ResearchIntentContract,
    build_contract_from_context,
    contract_fields_from_need_spec,
    format_contract_for_user,
    load_confirmed_contract,
    merge_contract_draft,
    save_contract_draft,
)
from autoad_researcher.assistant.v2.draft_service import load_research_draft_state
from autoad_researcher.assistant.v2.need_discovery import RequiredNeedSpec
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.reply_planner import plan_reply
from autoad_researcher.server.routes.chat import _assistant_delta_message, _assistant_done_message
from autoad_researcher.assistant.chat_facts import extract_confirmed_from_chat


def test_research_intent_contract_defaults_do_not_require_method_or_target_module():
    contract = ResearchIntentContract(run_id="run_contract")

    assert contract.task_domain == "anomaly_detection"
    assert contract.execution_mode == "plan_only"
    assert contract.user_improvement_hints == []
    assert contract.user_target_module_hints == []
    assert "modify_test_labels" in contract.forbidden_change_scope
    assert "change_metric_definition" in contract.forbidden_change_scope
    assert set(DEFAULT_FORBIDDEN_CHANGE_SCOPE).issubset(set(contract.forbidden_change_scope))


def test_build_contract_ready_for_plan_without_improvement_or_target_module(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    llm_context = {
        "confirmed_from_user": {
            "baseline": "PatchCore",
            "dataset": "MVTec AD",
            "metrics": ["image_level_auroc"],
        },
        "usable_evidence": [],
    }

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我的目标是提升指标效果，保持 baseline 原始评价协议。",
        llm_context=llm_context,
    )

    assert contract.research_goal == "提升 PatchCore 在 MVTec AD 上的 image_level_auroc"
    assert contract.baseline == "PatchCore"
    assert contract.dataset == "MVTec AD"
    assert contract.primary_metrics == ["image_level_auroc"]
    assert contract.primary_metric == "image_level_auroc"
    assert contract.success_criteria == "improve image_level_auroc under the same evaluation protocol"
    assert contract.user_improvement_hints == []
    assert contract.user_target_module_hints == []
    assert contract.ready_for_plan is True
    assert contract.ready_for_experiment_agents is False
    assert contract.missing_required_fields == []


def test_contract_cleans_patchcore_simplenet_conversation_state(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    transcript = [
        {"role": "user", "content": "pathcore为基线，然后指标AUROC，数据集mvtec；方法采取论文内的，你觉得什么方法好？"},
        {"role": "assistant", "content": "可以尝试特征适配器、合成异常特征、判别器校准。"},
        {"role": "user", "content": "这些想法都可以尝试，然后提升就是AUROC，到那时AUROC也有几种，选最主流的两种吧"},
        {"role": "user", "content": "基线仓库找pathcore的啊，我说了用户改进想法了，你都列上去啊，成功标准比就是提升AUROC比pathcore"},
    ]

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="基线仓库找pathcore的啊，我说了用户改进想法了，你都列上去啊，成功标准比就是提升AUROC比pathcore",
        transcript_tail=transcript,
        llm_context={
            "confirmed_from_user": {
                "baseline": "PatchCore",
                "dataset": "MVTec AD",
                "metrics": ["image_level_auroc"],
            },
            "usable_evidence": [
                {
                    "evidence_type": "paper_reading_summary",
                    "summary": "SimpleNet uses a Feature Adaptor, Gaussian noise to synthesize anomalous features, and a Discriminator.",
                }
            ],
        },
    )

    assert contract.research_goal == "提升 PatchCore 在 MVTec AD 上的 image_level_auroc, pixel_level_auroc"
    assert contract.primary_metrics == ["image_level_auroc", "pixel_level_auroc"]
    assert contract.success_criteria == (
        "improve image_level_auroc, pixel_level_auroc over the PatchCore baseline under the same evaluation protocol"
    )
    assert contract.user_improvement_hints == [
        "feature_adapter",
        "synthetic_anomaly_features",
        "discriminator_score_calibration",
    ]
    assert contract.preferred_method_hints == ["SimpleNet 论文方法"]


def test_draft_display_cleans_legacy_polluted_contract(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    (run_dir / "chat").mkdir(parents=True)
    (run_dir / "chat" / "transcript.jsonl").write_text(
        "\n".join([
            json.dumps({"role": "user", "content": "AUROC也有几种，选最主流的两种吧"}, ensure_ascii=False),
            json.dumps({"role": "user", "content": "这些想法都可以尝试，方法采取论文内的"}, ensure_ascii=False),
        ])
        + "\n",
        encoding="utf-8",
    )
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "evidence_index.jsonl").write_text(
        json.dumps({
            "source_id": "src_pdf",
            "support_level": "supported",
            "evidence_type": "paper_reading_summary",
            "artifact_path": "summary.md",
            "summary": "SimpleNet uses feature adaptor, Gaussian noise, and discriminator.",
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    save_contract_draft(run_dir, ResearchIntentContract(
        run_id="run_contract",
        research_goal="提升 baseline 在目标数据集上的表现",
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        success_criteria="成功标准 " + "聊天历史污染 " * 30 + "提升AUROC比pathcore",
        baseline_repo="https://github.com/amazon-science/patchcore-inspection",
    ))

    payload = load_research_draft_state(run_dir)

    fields = {item["field"]: item for item in payload["fields"]}
    assert fields["research_goal"]["value"] == "提升 PatchCore 在 MVTec AD 上的 图像级 AUROC、像素级 AUROC"
    assert fields["primary_metrics"]["value"] == "图像级 AUROC、像素级 AUROC"
    assert fields["success_criteria"]["value"] == "图像级 AUROC、像素级 AUROC 高于 PatchCore 基线（保持相同评估设置）"
    assert fields["user_improvement_hints"]["value"] == "特征适配器；合成异常特征；判别器/分数校准"
    assert "evaluation_protocol" not in fields


def test_draft_does_not_infer_baseline_from_source_only_repo_url(tmp_path: Path):
    run_dir = tmp_path / "run_source_only"
    (run_dir / "chat").mkdir(parents=True)
    (run_dir / "chat" / "transcript.jsonl").write_text(
        json.dumps(
            {
                "role": "user",
                "content": "https://github.com/amazon-science/patchcore-inspection.git；分析一下这个仓库，能clone",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    sources = run_dir / "sources"
    sources.mkdir(parents=True)
    (sources / "source_references.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "source_id": "src_repo",
                        "kind": "github_repo",
                        "user_label": "https://github.com/amazon-science/patchcore-inspection",
                        "status": "user_provided_not_ingested",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = load_research_draft_state(run_dir)

    fields = {item["field"]: item for item in payload["fields"]}
    assert fields["baseline"]["status"] == "missing"
    assert fields["baseline_repo"]["status"] == "missing"
    assert fields["baseline"]["value"] == "待补充"
    assert fields["baseline_repo"]["value"] == "待补充"


def test_build_contract_keeps_repo_analysis_readiness_separate(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    sources = run_dir / "sources"
    sources.mkdir(parents=True)
    (sources / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_repo",
                    "kind": "github_repo",
                    "user_label": "https://github.com/example/repo",
                    "status": "user_provided_not_ingested",
                }
            ],
        }),
        encoding="utf-8",
    )

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我想提升 PatchCore 在 MVTec AD 上的 image AUROC。",
        llm_context={"confirmed_from_user": {}},
    )

    assert contract.ready_for_plan is True
    assert contract.ready_for_repo_analysis is True
    assert contract.ready_for_experiment_agents is False
    assert contract.baseline_repo == "https://github.com/example/repo"


def test_orchestrator_writes_draft_then_confirms_existing_contract(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system_text = messages[0]["content"]
        user_text = messages[-1]["content"]
        if "TurnGateDecision JSON" in system_text:
            if user_text == "确认":
                return {"reply": json.dumps(_turn_gate_payload(
                    turn_type="contract_confirmation",
                    contract_action="confirm_contract",
                    allowed=False,
                ), ensure_ascii=False), "error": ""}
            return {"reply": json.dumps(_turn_gate_payload(), ensure_ascii=False), "error": ""}
        if "Need Discovery" in system_text:
            return {"reply": json.dumps(_need_spec_payload(
                baseline="PatchCore",
                dataset="MVTec AD",
                metrics=["image_level_auroc"],
            ), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps(_reply_payload("已记录。"), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    transcript_tail = [
        {"role": "user", "content": "baseline 是 PatchCore，数据集 MVTec AD，指标 image AUROC"},
    ]

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我的目标是提升指标效果，保持 baseline 原始评价协议。",
        transcript_tail=transcript_tail,
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.reply_kind == "intent_contract_confirmation"
    assert result.intent_contract["ready_for_plan"] is True
    assert (run_dir / CONTRACT_DRAFT_FILE).is_file()
    assert not (run_dir / CONTRACT_FILE).exists()
    assert "如果以上正确，请回复“确认”" in result.reply

    confirmed = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="确认",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert confirmed.reply_kind == "intent_contract_confirmed"
    assert confirmed.intent_contract_confirmed is True
    assert (run_dir / CONTRACT_FILE).is_file()
    loaded = load_confirmed_contract(run_dir)
    assert loaded is not None
    assert loaded.ready_for_plan is True


def test_format_contract_does_not_pressure_user_for_method_or_module():
    contract = ResearchIntentContract(
        run_id="run_contract",
        research_goal="提升 baseline 在目标数据集上的表现",
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        primary_metric="image_level_auroc",
        success_criteria="improve image_level_auroc under the same evaluation protocol",
        ready_for_plan=True,
    )

    text = format_contract_for_user(contract)

    assert "未提供；这不阻塞" in text
    assert "后续 experiment agents 会自动探索" in text
    assert "后续 repo/experiment agents 会定位" in text
    assert "你想怎么改" not in text
    assert "你要改哪个模块" not in text


def test_reply_planner_fallback_asks_goal_not_method():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intake"},
            "usable_evidence": [],
            "unparsed_sources": [],
            "readable_summaries": [],
        },
        "我想做异常检测",
    )

    assert "不需要你先设计具体方法" in reply
    assert "主要目标" in reply
    assert "你想怎么改" not in reply
    assert "你要改哪个模块" not in reply


def test_reply_planner_llm_prompt_requires_structured_json(monkeypatch):
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["messages"] = messages
        return {
            "reply": json.dumps({
                "reply_to_user": "请确认主要目标。",
                "contract_updates": {},
                "new_user_confirmed_fields": [],
                "missing_required_fields": ["primary_metrics"],
                "optional_hints_detected": {},
                "next_question": "你主要想优化什么？",
                "ready_for_confirmation": False,
                "ready_for_experiment_agents": False,
            }, ensure_ascii=False),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intake"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract", "execution_mode": "plan_only"},
        },
        "我想做异常检测",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    system_text = "\n".join(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert kind == "answer"
    assert len(reply) > 10  # has meaningful content
    assert "reply_to_user" not in reply
    assert "contract_updates" not in reply
    assert "missing_required_fields" not in reply
    assert "reply_to_user" in system_text
    assert "行为准则" in system_text


def test_reply_planner_streams_only_visible_reply_field(monkeypatch):
    streamed: list[str] = []

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        on_delta = kwargs.get("on_delta")
        assert on_delta is not None
        for chunk in [
            '{"reply_to_user":"已记录',
            '。", "contract_updates": {"baseline": "PatchCore"}, ',
            '"missing_required_fields": []}',
        ]:
            on_delta(chunk)
        return {
            "reply": json.dumps({
                "reply_to_user": "已记录。",
                "contract_updates": {"baseline": "PatchCore"},
                "missing_required_fields": [],
            }, ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
        on_delta=streamed.append,
    )

    assert kind == "answer"
    assert reply == "已记录。"
    assert "".join(streamed) == "已记录。"
    assert "contract_updates" not in "".join(streamed)
    assert "PatchCore" not in "".join(streamed)


def test_reply_planner_handles_reply_to_user_not_first_key_without_leaking(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": json.dumps({
                "contract_updates": {"baseline": "PatchCore"},
                "missing_required_fields": ["success_criteria"],
                "reply_to_user": "我只展示这句。",
                "next_question": "请确认成功标准。",
                "ready_for_plan": False,
            }, ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert reply == "我只展示这句。\n\n请确认成功标准。"
    assert "contract_updates" not in reply
    assert "missing_required_fields" not in reply
    assert "PatchCore" not in reply


def test_reply_stream_chunk_split_and_key_order_do_not_leak_internal_fields(monkeypatch):
    streamed: list[str] = []
    payload = json.dumps({
        "contract_updates": {"baseline": "PatchCore"},
        "missing_required_fields": ["success_criteria"],
        "reply_to_user": "已记录用户可见内容。",
        "next_question": "",
        "ready_for_plan": False,
    }, ensure_ascii=False)

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        on_delta = kwargs.get("on_delta")
        assert on_delta is not None
        for chunk in [payload[:11], payload[11:39], payload[39:58], payload[58:77], payload[77:]]:
            on_delta(chunk)
        return {"reply": payload, "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
        on_delta=streamed.append,
    )

    visible_stream = "".join(streamed)
    assert reply == "已记录用户可见内容。"
    assert visible_stream == "已记录用户可见内容。"
    assert "contract_updates" not in visible_stream
    assert "missing_required_fields" not in visible_stream
    assert "PatchCore" not in visible_stream


def test_hf2_reply_does_not_expose_raw_json(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": json.dumps({
                "reply_to_user": "我已记录 baseline 和数据集。",
                "contract_updates": {"dataset": "MVTec AD"},
                "missing_required_fields": ["dataset"],
                "next_question": "请确认主要指标。",
            }, ensure_ascii=False),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "我的数据集是 MVTec AD",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert reply == "我已记录 baseline 和数据集。\n\n请确认主要指标。"
    assert "{" not in reply
    assert "contract_updates" not in reply
    assert "missing_required_fields" not in reply


def test_reply_planner_non_json_internal_payload_uses_safe_fallback(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": (
                "我已记录。\n"
                'contract_updates: {"baseline": "PatchCore"}\n'
                'missing_required_fields: ["success_criteria"]\n'
            ),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "当前状态: intent" in reply
    assert "contract_updates" not in reply
    assert "missing_required_fields" not in reply
    assert "PatchCore" not in reply


def test_reply_planner_broken_json_internal_payload_uses_safe_fallback(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": '{"reply_to_user":"已记录。","contract_updates":{"baseline":"PatchCore"',
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "当前状态: intent" in reply
    assert "contract_updates" not in reply
    assert "PatchCore" not in reply


def test_assistant_delta_and_done_message_shapes_stay_compatible_for_reply_streaming():
    assert _assistant_delta_message("assistant_1", "hello") == {
        "type": "assistant.delta",
        "message_id": "assistant_1",
        "content": "hello",
    }
    assert _assistant_done_message("assistant_1", "answer", "done") == {
        "type": "assistant.done",
        "message_id": "assistant_1",
        "reply_kind": "answer",
        "content": "done",
    }


def test_reply_planner_parses_key_value_contract_payload(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": (
                'reply_to_user: "我只能看到解析不可用，不能读论文细节。"\n\n'
                "contract_updates: {}\n"
                'missing_required_fields: ["success_criteria"]\n'
                'next_question: ""\n'
                "ready_for_confirmation: false"
            ),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "parse_quality"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "unusable_parsed_sources": [{"source_id": "src_pdf", "user_label": "paper.pdf"}],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "你现在读论文，找到能做的方向",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert reply == "我只能看到解析不可用，不能读论文细节。"
    assert "reply_to_user" not in reply
    assert "contract_updates" not in reply


def test_chat_facts_latest_metric_correction_overrides_old_vram_focus():
    facts = extract_confirmed_from_chat([
        {"role": "user", "content": "我想测试 coreset sampling 是否能降低显存和运行时间，AUROC 不明显下降就可以。"},
        {"role": "assistant", "content": "已记录显存和运行时间。"},
        {"role": "user", "content": "不是，我就是想提升AUROC，pathcore的，但是要从论文里提取方法，或者思路"},
    ])

    assert facts["metrics"] == ["image_level_auroc"]


def test_need_discovery_metrics_override_stale_chat_fact_metrics(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_need_spec_payload(
            baseline="PatchCore",
            dataset="MVTec AD",
            metrics=["image_level_auroc"],
        ), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="不是，我就是想提升AUROC，pathcore的，但是要从论文里提取方法。",
        transcript_tail=[
            {"role": "user", "content": "我想测试 coreset sampling 是否能降低显存和运行时间。"},
            {"role": "assistant", "content": "已记录显存和运行时间。"},
        ],
        llm_context={"confirmed_from_user": {"metrics": ["peak_vram"]}},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert contract.primary_metrics == ["image_level_auroc"]
    assert contract.metric_intent.extraction_source == "llm"


def test_reply_fallback_uses_known_parse_errors_without_guessing():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "parse_quality"},
            "unparsed_sources": [],
            "usable_evidence": [],
            "readable_summaries": [],
            "pending_jobs": [],
            "failed_jobs": [],
            "unusable_parsed_sources": [
                {
                    "source_id": "src_pdf",
                    "user_label": "2303.15140v2.pdf",
                    "warnings": ["parse produced no readable paper.md; parsed text is not usable evidence"],
                    "fatal_errors": ["parse produced no readable paper.md"],
                    "parser_errors": [{"parser_name": "markitdown", "error": "markitdown unavailable: No module named 'markitdown'"}],
                }
            ],
        },
        "为什么失败了",
    )

    assert "parse produced no readable paper.md" in reply
    assert "markitdown unavailable" in reply
    assert "扫描" not in reply
    assert "复杂排版" not in reply


def test_parse_failure_question_bypasses_llm_speculation(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps({
            "reply_to_user": "可能是扫描版、加密/损坏，或者临时服务异常。",
            "contract_updates": {},
            "missing_required_fields": [],
            "next_question": "",
            "ready_for_confirmation": False,
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "parse_quality"},
            "unparsed_sources": [],
            "usable_evidence": [],
            "readable_summaries": [],
            "pending_jobs": [],
            "failed_jobs": [{"job_id": "job_000001", "job_type": "paper_parse_mineru", "error": "execution failed"}],
            "unusable_parsed_sources": [
                {
                    "source_id": "src_pdf",
                    "user_label": "2303.15140v2.pdf",
                    "warnings": ["parse produced no readable paper.md; parsed text is not usable evidence"],
                    "fatal_errors": ["parse produced no readable paper.md"],
                    "parser_errors": [{"parser_name": "markitdown", "error": "markitdown unavailable: No module named 'markitdown'"}],
                }
            ],
        },
        "为什么失败了",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "parse produced no readable paper.md" in reply
    assert "markitdown unavailable" in reply
    assert "请先确认主要目标" not in reply
    assert "扫描版" not in reply
    assert "加密/损坏" not in reply
    assert "临时服务异常" not in reply


def test_repo_failure_question_does_not_append_pdf_conclusion():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "idle"},
            "unparsed_sources": [],
            "usable_evidence": [],
            "readable_summaries": [],
            "pending_jobs": [],
            "failed_jobs": [
                {"job_id": "job_000002", "job_type": "git_clone", "error": "git_clone: git command failed: timed_out:"},
                {"job_id": "job_000004", "job_type": "git_clone", "error": "fatal: unable to access 'https://github.com/amazon-science/patchcore-inspection/': GnuTLS recv error (-110): The TLS connection was non-properly terminated."},
                {"job_id": "job_000003", "job_type": "repo_summarize", "error": "dependency failed: job_000002"},
            ],
            "unusable_parsed_sources": [
                {
                    "source_id": "src_pdf",
                    "user_label": "2303.15140v2.pdf",
                    "warnings": ["parse produced no readable paper.md"],
                }
            ],
        },
        "查看clone仓库失败原因",
    )

    assert "网络/TLS" in reply
    assert "不像是仓库不存在" in reply
    assert "git_clone(job_000002)" in reply
    assert "git_clone(job_000004)" in reply
    assert "dependency failed: job_000002" in reply
    assert "镜像 URL" in reply
    assert "zip/tar" in reply
    assert "web_search 镜像/候选仓库" not in reply
    assert "PDF" not in reply
    assert "论文方法细节证据" not in reply


def test_repo_failure_with_truncated_cloning_error_is_explained_as_transport_failure():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "idle"},
            "unparsed_sources": [],
            "usable_evidence": [],
            "readable_summaries": [],
            "pending_jobs": [],
            "failed_jobs": [
                {
                    "job_id": "job_000001",
                    "job_type": "git_clone",
                    "error": "git_clone: git command failed: tool_git_clone: failed: Cloning into 'repo'",
                },
                {"job_id": "job_000002", "job_type": "repo_summarize", "error": "dependency failed: job_000001"},
            ],
        },
        "clone失败了，你总结一下原因",
    )

    assert "网络/TLS" in reply
    assert "仓库不存在" in reply
    assert "dependency failed: job_000001" in reply


def test_hf2_contract_preserves_dataset_across_turns(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system_text = messages[0]["content"]
        user_text = messages[-1]["content"]
        if "TurnGateDecision JSON" in system_text:
            return {"reply": json.dumps(_turn_gate_payload(), ensure_ascii=False), "error": ""}
        if "Need Discovery" in system_text:
            metrics = (
                ["image_level_auroc", "pixel_level_auroc"]
                if "pixel AUROC" in user_text
                else ["image_level_auroc"]
            )
            return {"reply": json.dumps(_need_spec_payload(
                baseline="PatchCore",
                dataset="MVTec AD",
                metrics=metrics,
            ), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps(_reply_payload("已记录。"), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    first = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我想基于 PatchCore 做异常检测改进，主要想提升 MVTec AD 上的效果，先不要自动改代码，先帮我整理方案。",
        api_key="sk-test",
        provider_url="https://example.test",
    )
    assert first.intent_contract["dataset"] == "MVTec AD"

    second = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="主要看 image AUROC 和 pixel AUROC，成功标准是比原始 PatchCore 有提升，评价流程不能作弊，不能改测试集和指标定义。",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert second.intent_contract["baseline"] == "PatchCore"
    assert second.intent_contract["dataset"] == "MVTec AD"
    assert second.intent_contract["primary_metrics"] == ["image_level_auroc", "pixel_level_auroc"]
    assert second.intent_contract["primary_metric"] is None
    assert second.intent_contract["secondary_metrics"] == []
    assert second.intent_contract["metric_priority"] == "co_primary"


def test_hf2_contract_ready_after_metric_and_success(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    existing = ResearchIntentContract(
        run_id="run_contract",
        research_goal="提升 baseline 在目标数据集上的表现",
        baseline="PatchCore",
        dataset="MVTec AD",
        execution_mode="plan_only",
    )
    update = build_contract_from_context(
        run_dir=run_dir,
        user_input="主要看 image AUROC，成功标准是保持原始评价协议并提升指标。",
        llm_context={"confirmed_from_user": {}},
    )

    merged = merge_contract_draft(existing, update)

    assert merged.ready_for_plan is True
    assert merged.missing_required_fields == []
    assert merged.primary_metrics == ["image_level_auroc"]
    assert merged.primary_metric == "image_level_auroc"
    assert merged.success_criteria == "improve image_level_auroc under the same evaluation protocol"


def test_hf2_metric_extraction_image_and_pixel_co_primary(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    image_primary = build_contract_from_context(
        run_dir=run_dir,
        user_input="主要看 image AUROC 和 pixel AUROC。",
        llm_context={"confirmed_from_user": {}},
    )
    reversed_order = build_contract_from_context(
        run_dir=run_dir,
        user_input="主要看 pixel AUROC 和 image AUROC。",
        llm_context={"confirmed_from_user": {}},
    )

    assert image_primary.primary_metrics == ["image_level_auroc", "pixel_level_auroc"]
    assert image_primary.primary_metric is None
    assert image_primary.secondary_metrics == []
    assert image_primary.metric_priority == "co_primary"
    assert reversed_order.primary_metrics == ["pixel_level_auroc", "image_level_auroc"]
    assert reversed_order.primary_metric is None
    assert reversed_order.secondary_metrics == []
    assert reversed_order.metric_priority == "co_primary"


def test_hf2_metric_extraction_explicit_primary_and_secondary(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    image_primary = build_contract_from_context(
        run_dir=run_dir,
        user_input="image AUROC 为主，pixel AUROC 参考。",
        llm_context={"confirmed_from_user": {}},
    )
    pixel_primary = build_contract_from_context(
        run_dir=run_dir,
        user_input="pixel AUROC 为主，image AUROC 参考。",
        llm_context={"confirmed_from_user": {}},
    )

    assert image_primary.primary_metrics == ["image_level_auroc"]
    assert image_primary.primary_metric == "image_level_auroc"
    assert image_primary.secondary_metrics == ["pixel_level_auroc"]
    assert image_primary.metric_priority == "image_level_auroc_first"
    assert pixel_primary.primary_metrics == ["pixel_level_auroc"]
    assert pixel_primary.primary_metric == "pixel_level_auroc"
    assert pixel_primary.secondary_metrics == ["image_level_auroc"]
    assert pixel_primary.metric_priority == "pixel_level_auroc_first"


def test_hf2_llm_missing_fields_not_authoritative(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": json.dumps({
                "reply_to_user": "已记录。",
                "missing_required_fields": ["dataset", "primary_metrics"],
                "next_question": "还需要确认成功标准。",
            }, ensure_ascii=False),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {
                "run_id": "run_contract",
                "dataset": "MVTec AD",
                "primary_metrics": ["image_level_auroc"],
                "missing_required_fields": ["success_criteria"],
            },
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "dataset" not in reply
    assert "primary_metrics" not in reply
    assert "missing_required_fields" not in reply


def test_need_spec_maps_non_hardcoded_baseline_into_contract(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_need_spec_payload(
            baseline="EfficientAD",
            dataset="VisA",
            metrics=["image_level_auroc", "pixel_level_auroc"],
        ), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我要做一个视觉异常检测改进任务。",
        llm_context={"confirmed_from_user": {}},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert contract.baseline == "EfficientAD"
    assert contract.dataset == "VisA"
    assert contract.primary_metrics == ["image_level_auroc", "pixel_level_auroc"]
    assert contract.metric_priority == "co_primary"


def test_hf2_identity_question_does_not_chase_dataset(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    save_contract_draft(
        run_dir,
        ResearchIntentContract(
            run_id=run_dir.name,
            research_goal="提升 baseline 在目标数据集上的表现",
            baseline="PatchCore",
            primary_metrics=["image_level_auroc"],
            success_criteria="improve selected metrics",
            ready_for_plan=False,
            missing_required_fields=["dataset"],
        ),
    )

    result = ResearchOrchestratorV2.handle(run_dir, user_input="你是谁？")

    assert result.reply_kind == "answer"
    assert "dataset" not in result.reply
    assert "请补充" not in result.reply
    assert result.intent_contract["missing_required_fields"] == ["dataset"]


def test_hf2_user_identity_does_not_update_contract(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    save_contract_draft(
        run_dir,
        ResearchIntentContract(
            run_id=run_dir.name,
            baseline="PatchCore",
            missing_required_fields=["dataset"],
        ),
    )

    result = ResearchOrchestratorV2.handle(run_dir, user_input="我是人类！")

    assert result.intent_contract["baseline"] == "PatchCore"
    assert result.intent_contract["dataset"] is None
    assert "请补充" not in result.reply


def test_hf2_playful_message_not_contract_update(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(run_dir, user_input="你是无敌美少女")

    assert result.intent_contract == {}
    assert "研究合同" in result.reply
    assert "dataset" not in result.reply


def test_hf2_frustration_not_contract_update(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(run_dir, user_input="我草泥马")

    assert result.intent_contract == {}
    assert "请补充" not in result.reply
    assert "dataset" not in result.reply


def test_hf2_research_keyword_joke_without_api_is_unknown_not_contract_update(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(run_dir, user_input="你是 PatchCore 战神")

    assert result.intent_contract == {}
    assert "请补充" not in result.reply
    assert "dataset" not in result.reply


def test_hf2_research_keyword_joke_with_api_is_decided_by_turn_gate(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        if "TurnGateDecision JSON" in messages[0]["content"]:
            return {"reply": json.dumps(_turn_gate_payload(
                turn_type="joke",
                contract_action="answer_without_contract_update",
                allowed=False,
                instruction="自然回应，不追问合同字段。",
            ), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps(_reply_payload("自然回应，不追问合同字段。"), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="你是 PatchCore 战神哈哈哈",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.intent_contract == {}
    assert "请补充" not in result.reply
    assert "dataset" not in result.reply


def test_hf2_contextual_turn_with_api_can_be_allowed_by_turn_gate(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        if "TurnGateDecision JSON" in messages[0]["content"]:
            return {"reply": json.dumps(_turn_gate_payload(), ensure_ascii=False), "error": ""}
        if "Need Discovery" in messages[0]["content"]:
            return {"reply": json.dumps(_need_spec_payload(
                baseline="PatchCore",
                dataset="MVTec AD",
                metrics=["image_level_auroc"],
            ), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps({
            "reply_to_user": "已按刚才的设置整理。",
            "contract_updates": {},
            "new_user_confirmed_fields": [],
            "missing_required_fields": [],
            "optional_hints_detected": {},
            "next_question": "",
            "ready_for_confirmation": True,
            "ready_for_experiment_agents": False,
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="那就按刚刚那个来吧",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.intent_contract["baseline"] == "PatchCore"
    assert result.intent_contract["dataset"] == "MVTec AD"
    assert result.intent_contract["primary_metrics"] == ["image_level_auroc"]


def test_hf2_multi_metric_update_replaces_old_single_primary(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        if "TurnGateDecision JSON" in messages[0]["content"]:
            return {"reply": json.dumps(_turn_gate_payload(), ensure_ascii=False), "error": ""}
        if "Need Discovery" in messages[0]["content"]:
            return {"reply": json.dumps(_need_spec_payload(
                baseline="PatchCore",
                dataset="MVTec AD",
                metrics=["image_level_auroc", "pixel_level_auroc"],
            ), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps(_reply_payload("已记录。"), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    save_contract_draft(
        run_dir,
        ResearchIntentContract(
            run_id=run_dir.name,
            research_goal="提升 baseline 在目标数据集上的表现",
            baseline="PatchCore",
            dataset="MVTec AD",
            primary_metrics=["pixel_level_auroc"],
            primary_metric="pixel_level_auroc",
            metric_priority="single_primary",
            success_criteria="improve pixel_level_auroc",
            execution_mode="plan_only",
        ),
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="主要看 image AUROC 和 pixel AUROC，成功标准是比原始 PatchCore 有提升，评价流程不能作弊，不能改测试集和指标定义。",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.intent_contract["primary_metrics"] == ["image_level_auroc", "pixel_level_auroc"]
    assert result.intent_contract["primary_metric"] is None
    assert result.intent_contract["metric_priority"] == "co_primary"
    assert result.intent_contract["success_criteria"]
    assert result.intent_contract["evaluation_protocol"] == "keep baseline/original evaluation protocol; no test split or metric changes"


def test_hf2_contract_related_turn_still_asks_missing_fields(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        if "TurnGateDecision JSON" in messages[0]["content"]:
            return {"reply": json.dumps(_turn_gate_payload(), ensure_ascii=False), "error": ""}
        if "Need Discovery" in messages[0]["content"]:
            return {"reply": json.dumps(_incomplete_need_spec_payload(), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps(_reply_payload("还需要确认数据集和指标。", "你主要看哪些指标？"), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我想基于 PatchCore 做异常检测改进。",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.reply_kind == "answer"
    assert "主要目标" in result.reply or "指标" in result.reply
    assert result.intent_contract.get("missing_required_fields")


def test_need_spec_contract_mapping_prefers_source_priority():
    spec = RequiredNeedSpec.model_validate({
        "task_summary": "test",
        "inferred_task_type": "image_anomaly_detection_improvement",
        "current_stage_goal": "generate_plan",
        "needs": [
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "EfficientAD",
                "source": "llm_inferred",
                "confidence": 0.6,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "PatchCore",
                "source": "user",
                "confidence": 0.95,
                "blocking": False,
                "question_to_user": None,
            },
        ],
        "blocking_needs": [],
        "next_best_question": None,
        "ready_for_plan": True,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": True,
        "ready_for_patch": False,
        "ready_for_run": False,
    })

    assert contract_fields_from_need_spec(spec)["baseline"] == "PatchCore"


def test_no_api_key_contract_fallback_still_recognizes_patchcore_mvtec(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
        llm_context={"confirmed_from_user": {}},
    )

    assert contract.baseline == "PatchCore"
    assert contract.dataset == "MVTec AD"
    assert contract.primary_metrics == ["image_level_auroc"]


def test_user_confirmed_field_not_overwritten_by_llm_inferred(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_need_spec_payload(
            baseline="EfficientAD",
            dataset="VisA",
            metrics=["image_level_auroc"],
        ), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="继续这个视觉异常检测任务",
        llm_context={"confirmed_from_user": {"baseline": "PatchCore"}},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert contract.baseline == "PatchCore"
    assert contract.dataset == "VisA"


def _turn_gate_payload(
    *,
    turn_type: str = "contract_update",
    contract_action: str = "update_contract",
    allowed: bool = True,
    instruction: str | None = None,
) -> dict:
    return {
        "turn_type": turn_type,
        "contract_action": contract_action,
        "contract_update_allowed": allowed,
        "need_discovery_allowed": allowed,
        "save_draft_allowed": allowed,
        "user_intent_summary": "测试 turn gate 决策",
        "evidence_from_current_turn": ["test"],
        "evidence_from_context": [],
        "confidence": 0.9,
        "reason": "test",
        "next_reply_instruction": instruction,
    }


def _reply_payload(reply: str, question: str = "") -> dict:
    return {
        "reply_to_user": reply,
        "contract_updates": {},
        "new_user_confirmed_fields": [],
        "missing_required_fields": [],
        "primary_metrics": [],
        "secondary_metrics": [],
        "metric_priority": None,
        "optional_hints_detected": {},
        "next_question": question,
        "ready_for_confirmation": False,
        "ready_for_experiment_agents": False,
    }


def _incomplete_need_spec_payload() -> dict:
    return {
        "task_summary": "基于 PatchCore 做异常检测改进",
        "inferred_task_type": "image_anomaly_detection_improvement",
        "current_stage_goal": "generate_plan",
        "needs": [
            {
                "name": "research_goal",
                "category": "intent",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "提升 baseline 在目标数据集上的表现",
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "PatchCore",
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "dataset",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": None,
                "source": "unknown",
                "confidence": 0.0,
                "blocking": True,
                "question_to_user": "请确认数据集。",
            },
            {
                "name": "metrics",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": [],
                "source": "unknown",
                "confidence": 0.0,
                "blocking": True,
                "question_to_user": "你主要看哪些指标？",
            },
            {
                "name": "success_criteria",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": None,
                "source": "unknown",
                "confidence": 0.0,
                "blocking": True,
                "question_to_user": "成功标准是什么？",
            },
            {
                "name": "execution_mode",
                "category": "execution",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "plan_only",
                "source": "default",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
        ],
        "blocking_needs": ["dataset", "metrics", "success_criteria"],
        "next_best_question": "你主要看哪些指标？",
        "ready_for_plan": False,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": False,
        "ready_for_patch": False,
        "ready_for_run": False,
    }


def _need_spec_payload(*, baseline: str, dataset: str, metrics: list[str]) -> dict:
    return {
        "task_summary": f"基于 {baseline} 在 {dataset} 上做改进",
        "inferred_task_type": "image_anomaly_detection_improvement",
        "current_stage_goal": "generate_plan",
        "needs": [
            {
                "name": "research_goal",
                "category": "intent",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "提升 baseline 在目标数据集上的表现",
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": baseline,
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "dataset",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": dataset,
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "metrics",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": metrics,
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "success_criteria",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "improve selected metrics under the same evaluation protocol",
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "execution_mode",
                "category": "execution",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "plan_only",
                "source": "default",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
        ],
        "blocking_needs": [],
        "next_best_question": None,
        "ready_for_plan": True,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": True,
        "ready_for_patch": False,
        "ready_for_run": False,
    }
