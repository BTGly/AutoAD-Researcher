# 开发计划 R2：Facts Assembler 与 Evidence Index（对应 PR-R1）

## 1. 目标

从冻结的 `ReportSnapshot` 和 AutoAD 现有控制面装配 `ExperimentReportFactsV1`、`evidence_index.json`、`report_digest.json`。本阶段纯代码运行，不调用 LLM，不从自由文本创造科学结论。

## 2. 复用参考与真实输入

| 输入 | 真实来源 | 报告侧处理 |
|---|---|---|
| Session | `ExperimentSession` / `ExperimentSessionStore` | 读取身份、合同、环境和 revision |
| Attempt | `ExperimentAttemptStore`、Attempt artifact | 保留每个 Attempt 的运行状态和 retry lineage |
| Outcome | `OutcomeCard` | 直接读取，不重新计算权威结论 |
| 科学判断 | `ScientificValidityReport`、`ScientificAssessment` | 读取比较性、有效性和科学效果 |
| Candidate/Champion | `CandidateRegistry`、`ChampionPointer` | 读取候选和当前 Champion |
| 成本 | `CognitiveCostSummary` / `CognitiveCostSummaryBuilder` | 读取资源和认知成本 |
| 停止事实 | `StopDecision` | 读取停止原因，不由 assembler 推断 |
| Artifact | `ArtifactReferenceV2` | 保存带 SHA 的类型化引用 |

计划中不再使用不存在的 `ChampionStore`、`CostSummary`、`IdeaTreeStore` 等名称；如果某个事实在当前仓库没有权威来源，输出为缺失/未确定并记录原因，不自行补齐。

## 3. 新增文件

```text
src/autoad_researcher/reporting/
├── snapshot.py
├── facts.py
├── evidence.py
└── digest.py
```

文件数量保持少量，避免在没有第二种实现前拆出过多抽象层。

## 4. `ExperimentReportFactsV1`

新 Facts 模型必须与旧 `ReportFacts` 分离。建议包含：

```text
schema_version
run_id
session_id
research_objective
evaluation_contract
repository_and_environment
baseline
candidate_and_champion
ideas
attempts
primary_metrics
guardrail_metrics
validity
failed_attempts
non_comparable_attempts
stop_decision
cost_summary
uncertainties
source_refs
```

Facts 中的 `scientific_effect`、`evaluation_status`、`protocol_valid` 等值只能来自现有事实模型。Assembler 不把“提升”“无效”“建议继续”等自然语言结论写入事实字段。

## 5. 不完整情况

| 情况 | Facts 处理 |
|---|---|
| Attempt 没有 metrics | metrics 为空，科学状态保持 `INCONCLUSIVE` 或缺失原因 |
| `OOM`、失败、超时、丢失 | 保留到 `failed_attempts`，不能进入提升列表 |
| evaluator 未完成 | validity 标记为证据不足，不计算科学效果 |
| baseline/candidate 不可比 | 进入 `non_comparable_attempts`，delta 为 `null` |
| 只有定性 artifact | 保留 qualitative evidence，不伪造数值 |
| Champion 仍为 baseline | 明确记录 baseline Champion |
| stop decision 缺失 | 标记为 interim/unknown，不猜测停止原因 |
| 部分 seed 完成 | 记录成功、失败和缺失 seed，不能把部分结果写成完整实验 |

Arbor 的 partial report 只作为“缺失数据仍能产生可读结果”的参考；AutoAD 的 EvaluationContract、OutcomeCard 和 validity 语义仍是权威。

## 6. Evidence Index

`evidence_index.json` 不使用只有自然语言 `claim` 的松散结构。建议每个条目包含：

```text
evidence_id
evidence_kind
artifact_ref: ArtifactReferenceV2
source_object_id
field_path
attempt_id / idea_id（如适用）
summary
```

`evidence_id` 应由稳定身份组成，例如来源对象 ID、artifact ID 和字段路径的 canonical 表示；不能把生成时间作为 ID 输入。

写入前校验：

- 引用必须登记在 Snapshot 的 source inventory 中；
- 文件必须存在且 SHA 匹配；
- locator 必须 run-relative；
- 类型必须属于允许的 artifact 类型；
- 证据大小和日志读取范围受上限约束。
- Evidence 的 `fact_refs` 必须覆盖事实投影，而不只覆盖根 Attempt：Candidate、baseline、失败/不可比 Attempt、主/guardrail 指标和 validity 均应保持到冻结 Facts 字段的可解析映射。
- 从 `OutputManifest` 接入 output 前，先复核 manifest 自身的 canonical SHA-256；不在 reporting 层另建摘要算法。
- `patch_diff` 只允许既有 handoff 已复制到 Attempt 制品目录的 `patch.diff` 或 `final_patch.diff`。未登记制品不以 Git worktree diff 补齐。

## 7. Digest

`report_digest.json` 是给前端和 Discussion Agent 使用的摘要卡，不是新的事实来源。只放：

```text
研究目标
工程/执行/科学状态
Champion
核心指标摘要
Attempt 数量和失败数量
停止事实
不确定性
可用格式和报告身份
```

Digest 必须从 Facts 确定性生成，并保留 `facts_content_sha256` 和 `report_id`。

Digest 和 ReportPage 还必须分别呈现工程、执行和科学状态，以及核心指标。没有 `ScientificAssessment` 时写入 `evidence-insufficient` 不确定性；该状态不等同于失败、无效或无效果。

## 8. Hash 和写入

Facts 的 canonical 内容 hash 排除 `generated_at`、`updated_at` 等 volatile 字段；实际写出的文件另算 artifact hash。输出采用 AutoAD 现有原子写模式，不能覆盖已经冻结的事实文件。

## 9. 验收

- [ ] 相同 Snapshot 输入得到相同 Facts content hash。
- [ ] 失败、超时、OOM、不可比较 Attempt 都保留。
- [ ] NON_COMPARABLE Attempt 的 delta 为 `null`。
- [ ] 所有 Facts 中的关键事实都能解析到 Evidence，包括 Candidate、baseline、指标和 validity 投影。
- [ ] Evidence 不能引用 Snapshot 外的 artifact。
- [ ] OutputManifest 本身及其中每个 output 的 SHA 都通过复核后才进入 inventory。
- [ ] 没有已登记 patch artifact 时不制造 Git diff Evidence。
- [ ] Champion 使用 `CandidateRegistry` / `ChampionPointer` 的真实数据。
- [ ] 缺失事实显式标记，不通过默认字符串或示例数值补齐。
- [ ] 输出文件原子写入，失败不留下半截 JSON。

## 10. 不做什么

- 不调用 LLM。
- 不读取整个 stdout/stderr 作为 Facts。
- 不从日志正则推导科学结论。
- 不修改 OutcomeCard、ScientificAssessment 或现有控制面事实。
- 不直接生成“推荐下一步”；建议留给 Narrative/Discussion，并必须建立在 Facts 上。
