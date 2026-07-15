from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.intent_contract import load_contract_draft
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.source_action_planner import (
    SourceActionPlan,
    explicit_source_input_is_url_only,
    load_repository_hints,
    plan_source_actions,
)
from autoad_researcher.assistant.v2.source_service import classify_input
from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence, load_usable_evidence
from autoad_researcher.ui.sources import load_source_registry
from autoad_researcher.worker.main import _run_web_search


def _conversation_route_payload(*, source_action_plan: dict, user_input: str) -> dict:
    return {
        "turn_gate": {
            "turn_type": "source_intake",
            "contract_action": "answer_without_contract_update",
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
            "confirmation_action_proposal": "none",
            "task_profile_proposal": "general_research",
            "task_profile_evidence": None,
            "requires_need_discovery_enrichment": False,
            "suggested_task_title": None,
            "suggested_task_summary": None,
            "user_intent_summary": user_input,
            "evidence_from_current_turn": [],
            "evidence_from_context": [],
            "confidence": 0.9,
            "reason": "Explicit source action.",
            "next_reply_instruction": None,
        },
        "source_action_plan": source_action_plan,
        "task_profile_proposal": "general_research",
        "task_profile_evidence": None,
        "suggested_task_title": None,
        "suggested_task_summary": None,
        "requires_need_discovery_enrichment": False,
    }


def test_natural_language_repo_request_is_not_classified_by_source_service():
    assert classify_input("你先 clone pathcore 的 github 仓库吧") == "general_chat"
    assert classify_input("搜索 MVTec AD 上能迁移到 PatchCore 的方法") == "general_chat"


def test_url_only_detection_uses_the_exact_supplied_reference():
    assert explicit_source_input_is_url_only("https://example.test/paper?lang=zh#section") is True
    assert explicit_source_input_is_url_only("<https://gitlab.example.test/team/repo.git?ref=main>") is True
    assert explicit_source_input_is_url_only("请读取 https://example.test/paper") is False
    assert explicit_source_input_is_url_only("https://example.test/a https://example.test/b") is False


def test_source_action_planner_exposes_configured_repository_hints(tmp_path: Path):
    run_dir = tmp_path / "run_repo"
    run_dir.mkdir()

    hints = load_repository_hints(run_dir)

    assert hints
    assert hints[0].hint_id == "internal_benchmark_patchcore"
    assert hints[0].url == "https://github.com/amazon-science/patchcore-inspection"
    assert hints[0].source == "configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml"


def test_source_action_planner_uses_llm_for_natural_language_clone(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_repo"
    run_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["messages"] = messages
        return {
            "reply": json.dumps(
                {
                    "actions": [
                        {
                            "action_type": "git_clone",
                            "target": "PatchCore official repository",
                            "repository_hint_id": "internal_benchmark_patchcore",
                            "source_url": None,
                            "query": None,
                            "source_kind": "github_repo",
                            "confidence": 0.86,
                            "requires_confirmation": False,
                            "rationale": "用户明确要求 clone 当前 baseline 的 GitHub 仓库；上下文候选中有已配置的 PatchCore 仓库。",
                        }
                    ],
                    "user_visible_summary": "将登记并 clone PatchCore 仓库。",
                    "confidence": 0.86,
                    "reason": "Explicit clone request with matching repository hint.",
                },
                ensure_ascii=False,
            ),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    plan = plan_source_actions(
        run_dir=run_dir,
        user_input="你先 clone pathcore 的 github 仓库吧",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    system_text = "\n".join(message["content"] for message in captured["messages"] if message["role"] == "system")
    assert "不是关键词分类器" in system_text
    assert "repository_hints" in system_text
    assert isinstance(plan, SourceActionPlan)
    assert plan.actions[0].action_type == "git_clone"
    assert plan.actions[0].source_url == "https://github.com/amazon-science/patchcore-inspection"


def test_orchestrator_creates_repo_jobs_from_llm_source_action(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_repo"
    run_dir.mkdir()

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system_text = messages[0]["content"]
        if "ConversationRouter" in system_text:
            source_plan = {
                "actions": [
                    {
                        "action_type": "git_clone",
                        "target": "PatchCore official repository",
                        "repository_hint_id": "internal_benchmark_patchcore",
                        "source_url": None,
                        "query": None,
                        "source_kind": "github_repo",
                        "confidence": 0.86,
                        "requires_confirmation": False,
                        "rationale": "用户明确要求 clone 仓库。",
                    }
                ],
                "user_visible_summary": "将登记并 clone PatchCore 仓库。",
                "confidence": 0.86,
                "reason": "Explicit clone request.",
            }
            return {
                "reply": json.dumps(
                    _conversation_route_payload(
                        source_action_plan=source_plan,
                        user_input="你先 clone pathcore 的 github 仓库吧",
                    ),
                    ensure_ascii=False,
                ),
                "error": "",
            }
        if "Need Discovery" in system_text:
            return {
                "reply": json.dumps(
                    {
                        "schema_version": 1,
                        "task_type": "anomaly_detection_experiment",
                        "current_stage_goal": "generate_plan",
                        "needs": [],
                        "ready_for_stage": False,
                        "blocking_questions": [],
                        "discovery_notes": [],
                    },
                    ensure_ascii=False,
                ),
                "error": "",
            }
        return {"reply": json.dumps({"reply_to_user": "已开始处理。"}, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="你先 clone pathcore 的 github 仓库吧",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.reply_kind == "source_intake"
    assert "后台开始 clone" in result.reply
    assert result.created_sources[0]["kind"] == "github_repo"
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]

    jobs = load_pipeline_jobs(run_dir)
    assert [job["job_type"] for job in jobs] == ["git_clone", "repo_summarize"]
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://github.com/amazon-science/patchcore-inspection"


def test_orchestrator_treats_mirror_url_as_repository_source_without_llm(tmp_path: Path):
    run_dir = tmp_path / "run_mirror_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://gitee.com/example/patchcore-inspection.git",
    )

    assert result.reply_kind == "source_intake"
    assert result.created_sources[0]["kind"] == "github_repo"
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://gitee.com/example/patchcore-inspection"


def test_orchestrator_treats_bare_github_root_as_repository_without_llm(tmp_path: Path):
    run_dir = tmp_path / "run_bare_github_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://github.com/example/library-a",
    )

    assert result.reply_kind == "source_intake"
    assert result.created_sources[0]["kind"] == "github_repo"
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://github.com/example/library-a"


def test_orchestrator_treats_gitlab_git_url_as_repository_source_without_llm(tmp_path: Path):
    run_dir = tmp_path / "run_gitlab_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://gitlab.com/example-group/example-repo.git",
    )

    assert result.reply_kind == "source_intake"
    assert result.created_sources[0]["kind"] == "github_repo"
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://gitlab.com/example-group/example-repo"


def test_orchestrator_keeps_non_git_url_as_webpage_without_llm_repo_intent(tmp_path: Path):
    run_dir = tmp_path / "run_generic_code_host"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://code.example.edu/example-group/example-repo",
    )

    assert result.reply_kind == "source_intake"
    assert result.created_sources[0]["kind"] == "webpage"
    assert [job["job_type"] for job in result.created_jobs] == ["web_fetch", "web_markitdown"]


def test_plain_repo_analysis_url_is_not_registered_as_baseline_contract(tmp_path: Path):
    run_dir = tmp_path / "run_plain_repo_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://github.com/amazon-science/patchcore-inspection.git；分析一下这个仓库，能clone",
    )

    assert result.reply_kind == "source_intake"
    assert "代码仓库" in result.reply
    assert "基线仓库" not in result.reply
    assert result.intent_contract == {}
    assert load_contract_draft(run_dir) is None

    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://github.com/amazon-science/patchcore-inspection"
    assert "；" not in registry["sources"][0]["user_label"]
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]


def test_orchestrator_creates_web_search_job_from_llm_source_action(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_search"
    run_dir.mkdir()

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system_text = messages[0]["content"]
        if "ConversationRouter" in system_text:
            source_plan = {
                "actions": [
                    {
                        "action_type": "web_search",
                        "target": "candidate papers",
                        "source_url": None,
                        "query": "MVTec AD PatchCore AUROC improvement methods",
                        "repository_hint_id": None,
                        "source_kind": None,
                        "confidence": 0.9,
                        "requires_confirmation": False,
                        "rationale": "用户要求查找资料。",
                    }
                ],
                "user_visible_summary": "将搜索候选资料。",
                "confidence": 0.9,
                "reason": "Explicit search request.",
            }
            return {
                "reply": json.dumps(
                    _conversation_route_payload(
                        source_action_plan=source_plan,
                        user_input="帮我搜一下 PatchCore 有哪些能提升 AUROC 的方法",
                    ),
                    ensure_ascii=False,
                ),
                "error": "",
            }
        return {
            "reply": json.dumps(
                {
                    "schema_version": 1,
                    "task_type": "anomaly_detection_experiment",
                    "current_stage_goal": "generate_plan",
                    "needs": [],
                    "ready_for_stage": False,
                    "blocking_questions": [],
                    "discovery_notes": [],
                },
                ensure_ascii=False,
            ),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="帮我搜一下 PatchCore 有哪些能提升 AUROC 的方法",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.reply_kind == "source_intake"
    assert [job["job_type"] for job in result.created_jobs] == ["web_search"]
    assert result.created_jobs[0]["payload"]["query"] == "MVTec AD PatchCore AUROC improvement methods"


def test_explicit_mirror_search_does_not_auto_create_web_search_without_provider(tmp_path: Path):
    run_dir = tmp_path / "run_mirror"
    source_dir = run_dir / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_repo",
                    "kind": "github_repo",
                    "user_label": "https://github.com/amazon-science/patchcore-inspection",
                    "status": "user_provided_not_ingested",
                    "created_at": "2026-07-09T01:00:00+00:00",
                }
            ],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="如果是网络问题，能不能 websearch 对应的镜像仓库？",
    )

    assert [job["job_type"] for job in result.created_jobs] == []
    assert load_pipeline_jobs(run_dir) == []


def test_orchestrator_removes_latest_source_when_user_rejects_upload(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_remove"
    source_dir = run_dir / "sources" / "src_wrong"
    source_dir.mkdir(parents=True)
    (source_dir / "wrong.md").write_text("wrong material", encoding="utf-8")
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_wrong",
                    "kind": "markdown",
                    "user_label": "wrong.md",
                    "status": "uploaded_not_parsed",
                    "stored_path": "sources/src_wrong/wrong.md",
                    "created_at": "2026-07-09T01:00:00+00:00",
                }
            ],
        }),
        encoding="utf-8",
    )
    append_artifact_evidence(
        run_dir,
        source_id="src_wrong",
        artifact_path="sources/src_wrong/wrong.md",
        evidence_type="uploaded_text",
        parser_name="direct_upload",
        summary="wrong material",
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("LLM planner should not be called for direct source removal")

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fail_if_called)

    result = ResearchOrchestratorV2.handle(run_dir, user_input="我上传错了，这个不是我们要的")

    assert result.reply_kind == "source_deleted"
    assert "wrong.md" in result.reply
    assert load_source_registry(run_dir)["sources"] == []
    assert load_usable_evidence(run_dir) == []
    assert not source_dir.exists()


def test_worker_web_search_wraps_pipeline_job_for_material_subagent(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_search"
    run_dir.mkdir()
    captured: dict[str, object] = {}

    def fake_run(run_dir_arg, *, request, provider=None):
        captured["run_dir"] = run_dir_arg
        captured["request"] = request
        return {"status": "completed"}

    monkeypatch.setattr(
        "autoad_researcher.assistant.material_subagents.run_material_discovery_subagent",
        fake_run,
    )

    ok = _run_web_search(
        run_dir,
        {
            "job_id": "job_000123",
            "source_id": "search",
            "job_type": "web_search",
            "evidence_role": "candidate_source_only",
            "payload": {"query": "PatchCore AUROC methods"},
        },
    )

    assert ok is True
    assert captured["run_dir"] == run_dir
    assert captured["request"]["request_id"] == "job_000123"
    assert captured["request"]["payload"]["query"] == "PatchCore AUROC methods"
    assert captured["request"]["evidence_role"] == "candidate_source_only"
