from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_intent_summary import load_research_intent_summary
from autoad_researcher.assistant.v2.source_service import classify_input
from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence, load_usable_evidence
from autoad_researcher.ui.sources import load_source_registry
from autoad_researcher.worker.main import _run_web_search


def _allow_decision(**updates) -> dict:
    payload = {
        "dialogue_mode": "ask",
        "policy_assessment": {
            "decision": "allow",
            "category": "none",
            "reason": "",
            "safe_alternative": "",
        },
        "source_action": None,
        "task_action": None,
        "target_spec": None,
    }
    payload.update(updates)
    return payload


def _mock_two_call(monkeypatch, decision: dict, reply: dict) -> list[list[dict[str, str]]]:
    calls: list[list[dict[str, str]]] = []
    replies = iter([decision, reply])

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        calls.append(messages)
        return {
            "reply": json.dumps(next(replies), ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    return calls


def test_natural_language_repo_request_is_not_classified_by_source_service():
    assert classify_input("你先 clone pathcore 的 github 仓库吧") == "general_chat"
    assert classify_input("搜索 MVTec AD 上能迁移到 PatchCore 的方法") == "general_chat"


def test_orchestrator_does_not_use_llm_to_invent_repo_url(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_repo"
    run_dir.mkdir()

    calls = _mock_two_call(
        monkeypatch,
        _allow_decision(),
        {
            "reply_to_user": "我不会猜测具体仓库。请提供要分析的仓库 URL。",
            "summary": {
                "goal": "分析用户指定的仓库",
                "confirmed_facts": ["用户要求 clone PatchCore 仓库"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": "请提供要分析的仓库 URL。",
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="你先 clone pathcore 的 github 仓库吧",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.reply_kind == "answer"
    assert "请提供要分析的仓库 URL" in result.reply
    assert result.created_sources == []
    assert result.created_jobs == []
    assert load_pipeline_jobs(run_dir) == []
    assert load_source_registry(run_dir)["sources"] == []
    assert len(calls) == 2


def test_orchestrator_treats_mirror_url_as_repository_source_without_llm(tmp_path: Path):
    run_dir = tmp_path / "run_mirror_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://gitee.com/example/patchcore-inspection.git",
    )

    assert result.created_sources[0]["kind"] == "github_repo"
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://gitee.com/example/patchcore-inspection"


def test_orchestrator_treats_gitlab_git_url_as_repository_source_without_llm(tmp_path: Path):
    run_dir = tmp_path / "run_gitlab_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://gitlab.com/example-group/example-repo.git",
    )

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

    assert result.created_sources[0]["kind"] == "webpage"
    assert [job["job_type"] for job in result.created_jobs] == ["web_fetch", "web_markitdown"]


def test_plain_repo_analysis_url_is_not_registered_as_baseline_contract(tmp_path: Path):
    run_dir = tmp_path / "run_plain_repo_url"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://github.com/amazon-science/patchcore-inspection.git；分析一下这个仓库，能clone",
    )

    assert "基线仓库" not in result.reply
    assert result.intent_summary["goal"] == ""
    assert load_research_intent_summary(run_dir) is None

    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://github.com/amazon-science/patchcore-inspection"
    assert "；" not in registry["sources"][0]["user_label"]
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]


def test_orchestrator_does_not_create_web_search_from_dialogue_llm(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_search"
    run_dir.mkdir()

    calls = _mock_two_call(
        monkeypatch,
        _allow_decision(),
        {
            "reply_to_user": "可以先明确检索范围，再登记找到的材料。",
            "summary": {
                "goal": "寻找提升 PatchCore AUROC 的材料",
                "confirmed_facts": ["用户希望搜索 PatchCore AUROC 提升方法"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="帮我搜一下 PatchCore 有哪些能提升 AUROC 的方法",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.reply_kind == "answer"
    assert result.created_jobs == []
    assert load_pipeline_jobs(run_dir) == []
    assert len(calls) == 2


def test_orchestrator_routes_bare_github_url_to_repo_and_continues_dialogue(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_bare_github"
    run_dir.mkdir()
    calls = _mock_two_call(
        monkeypatch,
        _allow_decision(),
        {
            "reply_to_user": "仓库材料尚在处理；分析完成后我会基于证据继续对齐研究目标。",
            "summary": {
                "goal": "分析用户提供的代码仓库",
                "confirmed_facts": ["用户提供了 https://github.com/example/repository"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://github.com/example/repository",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.reply_kind == "answer"
    assert "仓库材料尚在处理" in result.reply
    assert result.created_sources[0]["kind"] == "github_repo"
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "https://github.com/example/repository"
    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]
    assert len(calls) == 2


@pytest.mark.parametrize("target_expression", [
    "level=2、problem_id=40",
    "level 2 的第 40 题",
    "L2 / P40",
    "二级问题 40",
    "level: 2, problem: 40",
    "KernelBench 第二层第 40 个算子",
])
def test_orchestrator_queues_typed_repository_target_from_natural_expression(
    monkeypatch,
    tmp_path: Path,
    target_expression: str,
):
    run_dir = tmp_path / "run_target"
    run_dir.mkdir()
    calls = _mock_two_call(
        monkeypatch,
        _allow_decision(
            dialogue_mode="plan",
            target_spec={
                "adapter_id": "kernelbench",
                "selectors": {"level": 2, "problem_id": 40},
            },
        ),
        {
            "reply_to_user": "我会先读取指定任务文件，再基于该文件内容分析。",
            "summary": {
                "goal": "分析 KernelBench 指定任务",
                "confirmed_facts": [f"用户指定 {target_expression}"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=(
            "https://github.com/ScalingIntelligence/KernelBench "
            f"请分析 {target_expression}，只做 plan_only"
        ),
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert len(calls) == 2
    assert [job["job_type"] for job in result.created_jobs] == [
        "git_clone",
        "repo_summarize",
        "repo_analyze",
    ]
    target_job = result.created_jobs[-1]
    assert target_job["payload"]["target_adapter_id"] == "kernelbench"
    assert target_job["payload"]["repository_target"] == {"level": 2, "problem_id": 40}
    assert target_job["payload"]["depends_on"] == result.created_jobs[0]["job_id"]
    assert not (run_dir / "code").exists()
    assert not (run_dir / "experiments" / "sessions").exists()


def test_exact_selector_text_without_typed_target_does_not_queue_analysis(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_no_typed_target"
    run_dir.mkdir()
    _mock_two_call(
        monkeypatch,
        _allow_decision(dialogue_mode="plan"),
        {
            "reply_to_user": "还不能确定目标。",
            "summary": {
                "goal": "分析 KernelBench",
                "confirmed_facts": ["用户写了 level=2、problem_id=40"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="https://github.com/ScalingIntelligence/KernelBench level=2 problem_id=40",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert [job["job_type"] for job in result.created_jobs] == ["git_clone", "repo_summarize"]


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


def test_orchestrator_returns_confirmable_typed_removal_without_deleting(monkeypatch, tmp_path: Path):
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

    calls = _mock_two_call(
        monkeypatch,
        _allow_decision(source_action={
            "action": "request_source_removal",
            "source_id": "src_wrong",
            "label_hint": "wrong.md",
            "reason": "用户明确要求撤回",
        }),
        {
            "reply_to_user": "你明确要求撤回 wrong.md；删除前需要确认。",
            "summary": {
                "goal": "",
                "confirmed_facts": ["用户明确要求撤回 wrong.md"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="刚发的不是目标论文，撤回它。",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.reply_kind == "answer"
    assert "src_wrong" in calls[0][0]["content"]
    assert result.source_action == {
        "action": "request_source_removal",
        "source_id": "src_wrong",
        "label_hint": "wrong.md",
        "reason": "用户明确要求撤回",
    }
    assert load_source_registry(run_dir)["sources"][0]["source_id"] == "src_wrong"
    assert load_usable_evidence(run_dir)[0]["source_id"] == "src_wrong"
    assert source_dir.exists()


def test_orchestrator_rejects_removal_action_for_unknown_source(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_unknown_remove"
    run_dir.mkdir()

    _mock_two_call(
        monkeypatch,
        _allow_decision(source_action={
            "action": "request_source_removal",
            "source_id": "src_invented",
            "label_hint": "",
            "reason": "",
        }),
        {
            "reply_to_user": "无法定位要删除的材料。",
            "summary": {
                "goal": "",
                "confirmed_facts": [],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="删除那个材料",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.source_action is None


def test_orchestrator_queues_one_authorized_source_reparse(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_reparse"
    source_dir = run_dir / "sources" / "src_paper"
    source_dir.mkdir(parents=True)
    (source_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (run_dir / "sources" / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [{
                "source_id": "src_paper",
                "kind": "paper_pdf",
                "user_label": "paper.pdf",
                "stored_path": "sources/src_paper/paper.pdf",
                "status": "parsed",
                "parse_attempts": [{"parse_attempt_id": "pa_old", "status": "ok"}],
                "active_parse_attempt_id": "pa_old",
            }],
        }),
        encoding="utf-8",
    )
    decision = _allow_decision(
        dialogue_mode="act_request",
        source_action={
            "action": "request_source_reparse",
            "source_id": "src_paper",
            "label_hint": "paper.pdf",
            "reason": "用户明确要求重新解析",
        },
    )
    reply = {
        "reply_to_user": "我会创建新的解析尝试，保留当前解析记录。",
        "summary": {
            "goal": "重新解析当前论文",
            "confirmed_facts": ["用户明确要求重新解析当前论文"],
            "inferred_facts": [],
            "unresolved_conflicts": [],
            "blocking_question": None,
        },
    }
    replies = iter([decision, reply, decision, reply])
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": json.dumps(next(replies), ensure_ascii=False), "error": ""},
    )

    first = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="重新解析最新版论文。",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )
    second = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="重新解析最新版论文。",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    jobs = load_pipeline_jobs(run_dir)
    assert [job["job_type"] for job in jobs] == ["paper_parse_mineru"]
    assert first.created_jobs[-1]["job_id"] == jobs[0]["job_id"]
    assert second.created_jobs == []
    assert first.source_permission is not None
    assert first.source_permission["permission_decision"] == "allow"
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["parse_attempts"][0]["parse_attempt_id"] == "pa_old"
    decisions = (run_dir / "assistant" / "permission_decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(decisions) == 2


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
