"""DirectIdeaBackend — 把用户明确指定的 idea 忠实转换为结构化候选项。

不调用 LLM，不扩写科研结论，不推断成本。
只处理 direct_user_idea 模式。
"""

from autoad_researcher.ideation.base import IdeaGenerationBackend
from autoad_researcher.schemas import (
    ArtifactReference,
    IdeaCandidate,
    IdeaContext,
    IdeaGenerationResult,
)


class DirectIdeaBackend(IdeaGenerationBackend):
    """把用户明确指定的 idea 转换为结构化候选项。

    不调用 LLM，不扩写科研结论，不推断成本。
    confidence=1.0 表示"忠实转录用户 idea"的置信度，
    不是方法有效性的置信度。
    """

    def generate_ideas(self, *, context: IdeaContext) -> IdeaGenerationResult:
        if context.route.mode != "direct_user_idea":
            raise ValueError(
                f"DirectIdeaBackend only supports direct_user_idea mode, "
                f"got {context.route.mode!r}"
            )

        user_idea = (context.clarified_task.user_idea or "").strip()
        if not user_idea:
            raise ValueError("direct user idea is missing or empty")

        candidate = IdeaCandidate(
            idea_id="user_idea",
            title=_make_title(user_idea),
            description=user_idea,
            insertion_point=user_idea,
            rationale=(
                "The candidate directly preserves the "
                "user-specified implementation idea. "
                "Scientific validity and transferability "
                "have not yet been evaluated."
            ),
            expected_benefits=[],
            implementation_risks=["具体代码接口和张量形状尚未验证"],
            scientific_risks=["该方案尚未经过迁移可行性与科研有效性检查"],
            assumptions=["用户提供的实现描述足以进入后续有效性检查"],
            minimum_experiment=(
                "在保持 baseline、dataset、数据划分和 "
                "evaluation protocol 不变的前提下，"
                "只应用用户指定改动并运行最小对照实验"
            ),
            estimated_cost="unknown",
            confidence=1.0,
            evidence=[
                ArtifactReference(
                    artifact="clarified_task.json",
                    locator="user_idea",
                ),
            ],
        )

        return IdeaGenerationResult(
            run_id=context.run_id,
            mode="direct_user_idea",
            candidates=[candidate],
            disagreements=[],
            recommended_candidate_ids=["user_idea"],
        )


def _make_title(user_idea: str) -> str:
    normalized = " ".join(user_idea.split())
    if len(normalized) <= 48:
        return normalized
    return normalized[:45] + "..."
