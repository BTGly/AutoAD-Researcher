"""IntentClarifier — 基于已落盘事实生成澄清结果。

读取 input_task.yaml 和可选的 paper_summary / repo_summary，
调用 backend 生成 clarified_task.json。
"""

from pathlib import Path

from autoad_researcher.clarifiers.base import IntentClarifierBackend
from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.schemas import (
    ClarificationContext,
    InputTask,
    PaperSummary,
    RepositorySummary,
)

# 用户明确设置的字段，backend 不能改写
_IMMUTABLE_FIELDS = (
    "target_domain",
    "user_idea",
    "baseline",
    "dataset",
    "compute_budget",
)


def _revalidate_result(raw_result) -> "ClarifiedTask":
    """强制完整 Pydantic 校验，防止 model_copy() / model_construct() 绕过。"""
    from autoad_researcher.schemas import ClarifiedTask

    if isinstance(raw_result, ClarifiedTask):
        payload = raw_result.model_dump(mode="json")
    else:
        payload = raw_result
    return ClarifiedTask.model_validate(payload)


def _validate_candidate_references(
    result: "ClarifiedTask",
    *,
    task: "InputTask",
    paper_summary: "PaperSummary | None",
    repo_summary: "RepositorySummary | None",
) -> None:
    """校验 candidate evidence references 与当前 context 一致。"""
    for candidate in result.baseline_candidates:
        for evidence in candidate.evidence:
            for ref in evidence.references:
                if ref.source_id is not None and ref.source_id not in task.source_ids:
                    raise ValueError(
                        "candidate reference source_id is not referenced by input task"
                    )
                if ref.artifact == "repo_summary.json":
                    if repo_summary is None:
                        raise ValueError("candidate references missing repo_summary.json")
                    if ref.source_id != repo_summary.source_id:
                        raise ValueError("candidate repo source_id mismatch")
                if ref.artifact == "paper_summary.json":
                    if paper_summary is None:
                        raise ValueError("candidate references missing paper_summary.json")
                    if ref.source_id != paper_summary.source_id:
                        raise ValueError("candidate paper source_id mismatch")


class IntentClarifier:
    """基于已落盘事实的意图澄清 core service。"""

    def __init__(
        self,
        backend: IntentClarifierBackend,
        runs_root: str | Path = "runs",
    ) -> None:
        self._backend = backend
        self._artifacts = ArtifactStore(runs_root=runs_root)

    def run(self, run_id: str) -> StageResult:
        # --- 读取必需输入 ---
        task = self._artifacts.read_yaml_model(run_id, "input_task.yaml", InputTask)
        if task.run_id != run_id:
            raise ValueError("input task run_id mismatch")

        # --- 读取可选 Reader artifact ---
        paper_summary = self._read_optional(
            run_id, "paper_summary.json", PaperSummary, task
        )
        repo_summary = self._read_optional(
            run_id, "repo_summary.json", RepositorySummary, task
        )

        # --- 构造 context 并调用 backend ---
        context = ClarificationContext(
            run_id=run_id,
            task=task,
            paper_summary=paper_summary,
            repo_summary=repo_summary,
        )

        raw_result = self._backend.clarify(context=context)
        result = _revalidate_result(raw_result)

        # --- 校验 backend 输出 ---
        if result.run_id != run_id:
            raise ValueError("clarified task run_id mismatch")

        if result.original_request != task.request:
            raise ValueError("clarifier must preserve original request")

        if result.source_ids != task.source_ids:
            raise ValueError("clarifier must preserve source_ids")

        for field in _IMMUTABLE_FIELDS:
            if getattr(result, field) != getattr(task, field):
                raise ValueError(
                    f"clarifier must not rewrite explicit user field: {field}"
                )

        if result.constraints != task.constraints:
            raise ValueError("clarifier must preserve user constraints")

        if result.metrics:
            raise ValueError(
                "clarifier must not select metrics before user confirmation"
            )

        # baseline decision guards
        if task.baseline is not None:
            if result.baseline != task.baseline:
                raise ValueError("clarifier must not rewrite explicit user baseline")
            if result.baseline_decision is None:
                raise ValueError("confirmed baseline requires baseline_decision")
            if result.baseline_decision.source != "user_provided":
                raise ValueError("explicit user baseline must be user_provided")
        else:
            if result.baseline is not None:
                raise ValueError("clarifier must not select baseline without user confirmation")
            if result.baseline_decision is not None:
                raise ValueError("unconfirmed baseline cannot have a decision")

        # candidate reference context validation
        _validate_candidate_references(
            result, task=task, paper_summary=paper_summary, repo_summary=repo_summary,
        )

        # --- 写入 ---
        self._artifacts.write_json(run_id, "clarified_task.json", result)

        return StageResult(
            run_id=run_id,
            stage="intent_clarification",
            status="success",
            artifacts=["clarified_task.json"],
            metadata={
                "backend": self._backend.__class__.__name__,
                "clarification_status": result.status,
                "question_count": len(result.questions),
                "blocking_question_count": sum(
                    1 for m in result.missing_information if m.blocking
                ),
                "paper_summary_present": paper_summary is not None,
                "repo_summary_present": repo_summary is not None,
            },
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _read_optional(self, run_id: str, filename: str, model_cls, task: InputTask):
        if not self._artifacts.exists(run_id, filename):
            return None

        obj = self._artifacts.read_model(run_id, filename, model_cls)

        if obj.run_id != run_id:
            raise ValueError(f"{filename} run_id mismatch")

        if obj.source_id not in task.source_ids:
            raise ValueError(
                f"{filename} source_id is not referenced by input task"
            )

        return obj
