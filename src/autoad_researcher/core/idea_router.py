"""IdeaSourceRouter — 根据已澄清事实决定 idea 生成模式。

读取 clarified_task.json 和可选的 Reader artifact，
输出 idea_context.json 作为后续 Idea backend 的统一输入快照。
"""

from pathlib import Path

from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.schemas import (
    ClarifiedTask,
    IdeaContext,
    IdeaMode,
    IdeaRouteDecision,
    PaperSummary,
    RepositorySummary,
)


class IdeaSourceRouter:
    """决定当前 run 应进入哪种 idea 生成模式。

    路由规则：
    - blocking clarification → 拒绝路由
    - 显式 requested_mode → 使用显式模式
    - user_idea 为空 → multi_agent_exploration
    - user_idea 非空 → idea_decomposition
    - direct_user_idea 只能由显式请求，不自动进入
    """

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._artifacts = ArtifactStore(runs_root=runs_root)

    def run(
        self,
        run_id: str,
        *,
        requested_mode: IdeaMode | None = None,
    ) -> StageResult:
        # --- 读取 clarified task ---
        clarified = self._artifacts.read_model(
            run_id, "clarified_task.json", ClarifiedTask
        )
        if clarified.run_id != run_id:
            raise ValueError("clarified task run_id mismatch")

        # --- 阻塞检查 ---
        if clarified.status == "needs_blocking_input":
            raise ValueError(
                "cannot route ideas while blocking clarification input remains"
            )

        # --- 路由决策 ---
        route = _select_route(clarified, requested_mode)

        # --- 读取可选 Reader artifact ---
        paper_summary = self._read_optional(
            run_id, "paper_summary.json", PaperSummary, clarified
        )
        repo_summary = self._read_optional(
            run_id, "repo_summary.json", RepositorySummary, clarified
        )

        # --- 构造快照 ---
        context = IdeaContext(
            run_id=run_id,
            route=route,
            clarified_task=clarified,
            paper_summary=paper_summary,
            repo_summary=repo_summary,
        )

        # --- 写入 ---
        self._artifacts.write_json(run_id, "idea_context.json", context)

        return StageResult(
            run_id=run_id,
            stage="idea_routing",
            status="success",
            artifacts=["idea_context.json"],
            metadata={
                "mode": route.mode,
                "requested_mode": route.requested_mode,
                "paper_summary_present": paper_summary is not None,
                "repo_summary_present": repo_summary is not None,
                "clarification_status": clarified.status,
            },
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _read_optional(self, run_id, filename, model_cls, clarified):
        if not self._artifacts.exists(run_id, filename):
            return None

        obj = self._artifacts.read_model(run_id, filename, model_cls)

        if obj.run_id != run_id:
            raise ValueError(f"{filename} run_id mismatch")

        if obj.source_id not in clarified.source_ids:
            raise ValueError(f"{filename} source_id not in clarified source_ids")

        return obj


# ------------------------------------------------------------------
# 路由规则（纯函数）
# ------------------------------------------------------------------


def _select_route(
    clarified: ClarifiedTask,
    requested_mode: IdeaMode | None,
) -> IdeaRouteDecision:
    # 显式模式校验
    if requested_mode is not None:
        if requested_mode in {"direct_user_idea", "idea_decomposition"} and not clarified.user_idea:
            raise ValueError(
                f"{requested_mode} requires user_idea, but none is present"
            )
        return IdeaRouteDecision(
            mode=requested_mode,
            requested_mode=requested_mode,
            reason="Idea mode explicitly requested",
        )

    # 默认路由
    if clarified.user_idea is None:
        return IdeaRouteDecision(
            mode="multi_agent_exploration",
            reason="no user idea provided; candidate exploration required",
        )

    return IdeaRouteDecision(
        mode="idea_decomposition",
        reason="user idea present but no explicit direct route selected",
    )
