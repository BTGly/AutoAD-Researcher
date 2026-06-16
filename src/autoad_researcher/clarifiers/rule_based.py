"""RuleBasedIntentClarifierBackend — 基于规则的 deterministic clarifier。

不调用 LLM，只做事实汇总和缺口识别。
作为 CI 回归基线和未来智能 backend 的对照基准。
"""

from typing import Any

from autoad_researcher.clarifiers.base import IntentClarifierBackend
from autoad_researcher.schemas import (
    ArtifactReference,
    ClarificationContext,
    ClarificationQuestion,
    ClarifiedTask,
    ConfirmedDecision,
    DecisionCandidate,
    DecisionEvidence,
    KnownFact,
    MissingInformation,
)


class RuleBasedIntentClarifierBackend(IntentClarifierBackend):
    """基于规则的事实汇总和缺口识别。

    规则：
    - 从 input_task、paper_summary、repo_summary 中汇总 known facts
    - 只针对真正缺失的字段生成问题
    - baseline/dataset/metric 只能建议候选值，不能自动选择
    - user_idea 缺失不是错误
    - target_domain 缺失是阻塞问题
    """

    def clarify(self, *, context: ClarificationContext) -> ClarifiedTask:
        task = context.task
        known: list[KnownFact] = []
        missing: list[MissingInformation] = []
        questions: list[ClarificationQuestion] = []
        candidates: list[DecisionCandidate] = []
        decision: ConfirmedDecision | None = None

        self._gather_task_facts(task, known)

        # baseline provenance
        if task.baseline is not None:
            decision = ConfirmedDecision(
                value=task.baseline,
                source="user_provided",
                evidence="input_task.yaml:baseline",
            )
        else:
            candidates = self._gather_baseline_candidates(context)

        self._gather_paper_facts(context, known)
        self._gather_repo_facts(context, known)

        self._check_gaps(context, missing, questions, candidates)

        return ClarifiedTask(
            run_id=context.run_id,
            status=self._derive_status(missing, questions),
            original_request=task.request,
            source_ids=list(task.source_ids),
            target_domain=task.target_domain,
            user_idea=task.user_idea,
            baseline=task.baseline,
            dataset=task.dataset,
            metrics=[],
            compute_budget=task.compute_budget,
            constraints=list(task.constraints),
            baseline_candidates=candidates,
            baseline_decision=decision,
            known_facts=known,
            missing_information=missing,
            questions=questions,
        )

    # ------------------------------------------------------------------
    # Fact gathering
    # ------------------------------------------------------------------

    def _gather_task_facts(self, task: Any, known: list[KnownFact]) -> None:
        ref = [ArtifactReference(artifact="input_task.yaml", locator="request")]
        known.append(KnownFact(
            fact_id="original_request",
            category="task_scope",
            statement=f"用户原始请求：{task.request[:120]}",
            references=ref,
        ))

        for field, category in [
            ("target_domain", "domain"),
            ("user_idea", "method"),
            ("baseline", "baseline"),
            ("dataset", "dataset"),
            ("compute_budget", "resources"),
        ]:
            value = getattr(task, field, None)
            if value:
                known.append(KnownFact(
                    fact_id=f"user_{field}",
                    category=category,
                    statement=f"用户指定 {field}: {value}",
                    references=[ArtifactReference(artifact="input_task.yaml", locator=field)],
                ))

        for i, c in enumerate(task.constraints):
            known.append(KnownFact(
                fact_id=f"constraint_{i}",
                category="scientific_validity",
                statement=f"用户约束：{c}",
                references=[ArtifactReference(artifact="input_task.yaml", locator=f"constraints[{i}]")],
            ))

    def _gather_paper_facts(self, context: ClarificationContext, known: list[KnownFact]) -> None:
        paper = context.paper_summary
        if paper is None:
            return

        def _ref(field: str) -> list[ArtifactReference]:
            return [ArtifactReference(
                artifact="paper_summary.json",
                locator=field,
                source_id=paper.source_id,
            )]

        known.append(KnownFact(
            fact_id="paper_research_problem",
            category="task_scope",
            statement=f"论文研究问题：{paper.research_problem}",
            references=_ref("research_problem"),
        ))
        known.append(KnownFact(
            fact_id="paper_core_idea",
            category="method",
            statement=f"论文核心思路：{paper.core_idea}",
            references=_ref("core_idea"),
        ))
        if paper.datasets:
            known.append(KnownFact(
                fact_id="paper_datasets",
                category="dataset",
                statement=f"论文使用数据集：{', '.join(paper.datasets)}",
                references=_ref("datasets"),
            ))
        if paper.metrics:
            known.append(KnownFact(
                fact_id="paper_metrics",
                category="metrics",
                statement=f"论文使用指标：{', '.join(paper.metrics)}",
                references=_ref("metrics"),
            ))
        if paper.potential_transfer_points:
            known.append(KnownFact(
                fact_id="paper_transfer_points",
                category="method",
                statement=f"潜在迁移点：{', '.join(paper.potential_transfer_points)}",
                references=_ref("potential_transfer_points"),
            ))

    def _gather_repo_facts(self, context: ClarificationContext, known: list[KnownFact]) -> None:
        repo = context.repo_summary
        if repo is None:
            return

        def _ref(field: str) -> list[ArtifactReference]:
            return [ArtifactReference(
                artifact="repo_summary.json",
                locator=field,
                source_id=repo.source_id,
            )]

        if repo.baseline_methods:
            known.append(KnownFact(
                fact_id="repo_baselines",
                category="baseline",
                statement=f"仓库中包含 baseline：{', '.join(repo.baseline_methods)}",
                references=_ref("baseline_methods"),
            ))
        if repo.protected_paths:
            known.append(KnownFact(
                fact_id="repo_protected",
                category="scientific_validity",
                statement=f"受保护文件：{', '.join(repo.protected_paths)}",
                references=_ref("protected_paths"),
            ))

    # ------------------------------------------------------------------
    # Baseline candidate gathering
    # ------------------------------------------------------------------

    def _gather_baseline_candidates(
        self,
        context: ClarificationContext,
    ) -> list[DecisionCandidate]:
        """Collect baseline candidates from repo and paper, merge duplicates."""
        paper = context.paper_summary
        repo = context.repo_summary
        # (casefolded_key, display_value, source, evidence)
        entries: list[tuple[str, str, str, DecisionEvidence]] = []

        if repo and repo.baseline_methods:
            for value in repo.baseline_methods:
                v = value.strip()
                if not v:
                    continue
                entries.append((v.casefold(), v, "repo_detected", DecisionEvidence(
                    source="repo_detected",
                    rationale=f"仓库配置声明 {v} 模型",
                    references=[ArtifactReference(
                        artifact="repo_summary.json", locator="baseline_methods", source_id=repo.source_id,
                    )],
                )))

        if paper and paper.compared_methods:
            for value in paper.compared_methods:
                v = value.strip()
                if not v:
                    continue
                entries.append((v.casefold(), v, "paper_mentioned", DecisionEvidence(
                    source="paper_mentioned",
                    rationale=f"论文将 {v} 作为对比或依赖方法",
                    references=[ArtifactReference(
                        artifact="paper_summary.json", locator="compared_methods", source_id=paper.source_id,
                    )],
                )))

        source_order = {"repo_detected": 0, "paper_mentioned": 1, "history_detected": 2, "system_recommended": 3}
        by_key: dict[str, tuple[str, list[DecisionEvidence]]] = {}
        for key, display, src, ev in entries:
            if key not in by_key:
                by_key[key] = (display, [])
            by_key[key][1].append(ev)

        candidates: list[DecisionCandidate] = []
        for key, (display, ev_list) in by_key.items():
            ev_list.sort(key=lambda e: source_order.get(e.source, 99))
            candidates.append(DecisionCandidate(value=display, evidence=ev_list))
        return candidates

    # ------------------------------------------------------------------
    # Gap detection
    # ------------------------------------------------------------------

    def _check_gaps(
        self,
        context: ClarificationContext,
        missing: list[MissingInformation],
        questions: list[ClarificationQuestion],
        candidates: list[DecisionCandidate] | None = None,
    ) -> None:
        task = context.task
        paper = context.paper_summary
        repo = context.repo_summary

        # target_domain — blocking
        if task.target_domain is None:
            self._add_missing(missing, questions,
                item_id="missing_target_domain",
                category="domain",
                field="target_domain",
                reason="需要明确异常检测子方向才能进行方法迁移判断",
                blocking=True,
                question="这项任务属于哪种异常检测场景？",
                why_needed="需要确定任务域才能选择迁移策略和数据集",
                answer_type="free_text",
            )

        # baseline — non-blocking, suggest from repo/paper
        if task.baseline is None:
            options: list[str] = []
            if candidates:
                options = [c.value for c in candidates]
            refs = []
            for c in (candidates or []):
                for ev in c.evidence:
                    refs.extend(ev.references)
            self._add_missing(missing, questions,
                item_id="missing_baseline",
                category="baseline",
                field="baseline",
                reason="实验规划需要明确或确认 baseline 模型",
                blocking=False,
                question="你希望以哪个模型作为 baseline？",
                why_needed="需要确定 baseline 才能设计对照实验",
                options=options,
                suggested_values=options,
                references=refs,
                answer_type="single_choice" if options else "free_text",
            )

        # dataset — non-blocking, suggest from paper
        if task.dataset is None:
            options = paper.datasets if paper else []
            refs = []
            if paper:
                refs = [ArtifactReference(
                    artifact="paper_summary.json",
                    locator="datasets",
                    source_id=paper.source_id,
                )]
            self._add_missing(missing, questions,
                item_id="missing_dataset",
                category="dataset",
                field="dataset",
                reason="需要确定验证数据集",
                blocking=False,
                question="你希望使用哪个数据集进行验证？",
                why_needed="需要确定数据集才能进行实验",
                options=options,
                suggested_values=options,
                references=refs,
                answer_type="single_choice" if options else "free_text",
            )

        # metrics — non-blocking, multi-choice from paper
        options = paper.metrics if paper else []
        refs = []
        if paper:
            refs = [ArtifactReference(
                artifact="paper_summary.json",
                locator="metrics",
                source_id=paper.source_id,
            )]
        self._add_missing(missing, questions,
            item_id="missing_metrics",
            category="metrics",
            field="metrics",
            reason="需要确认评价指标",
            blocking=False,
            question="这次最小验证使用哪些评价指标？",
            why_needed="需要确定评价指标才能判断实验结果",
            options=options,
            suggested_values=options,
            references=refs,
            answer_type="multiple_choice" if options else "free_text",
        )

        # compute_budget — non-blocking
        if task.compute_budget is None:
            self._add_missing(missing, questions,
                item_id="missing_compute_budget",
                category="resources",
                field="compute_budget",
                reason="需要了解可用计算资源",
                blocking=False,
                question="本次验证可以使用哪些计算资源和时间预算？",
                why_needed="需要了解资源约束才能设计实验规模",
                answer_type="free_text",
            )

        # user_idea — NOT a gap (legal to be None)

    def _add_missing(
        self,
        missing: list[MissingInformation],
        questions: list[ClarificationQuestion],
        *,
        item_id: str,
        category: str,
        field: str,
        reason: str,
        blocking: bool,
        question: str,
        why_needed: str,
        answer_type: str,
        options: list[str] | None = None,
        suggested_values: list[str] | None = None,
        references: list[ArtifactReference] | None = None,
    ) -> None:
        missing.append(MissingInformation(
            item_id=item_id,
            category=category,
            field=field,
            reason=reason,
            blocking=blocking,
            suggested_values=suggested_values or [],
            references=references or [],
        ))

        questions.append(ClarificationQuestion(
            question_id=f"q_{item_id}",
            missing_item_id=item_id,
            question=question,
            why_needed=why_needed,
            answer_type=answer_type,
            options=options or [],
        ))

    # ------------------------------------------------------------------
    # Status derivation
    # ------------------------------------------------------------------

    def _derive_status(self, missing: list[MissingInformation], questions: list[ClarificationQuestion]) -> str:
        if any(m.blocking for m in missing):
            return "needs_blocking_input"
        if questions:
            return "has_nonblocking_questions"
        return "ready"
