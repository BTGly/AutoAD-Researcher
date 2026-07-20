# 开发计划 R2：Facts Assembler 与 Evidence Index（对应 PR-R1）

## 1. 目标

从冻结的 `ReportSnapshot` 装配 `ExperimentReportFactsV1`、`evidence_index.json`、`report_digest.json`。本阶段纯代码运行，不调用 LLM，不从自由文本创造科学结论；Facts assembler 不再读取实时控制面。

## 2. 复用参考与真实输入

| 输入 | 真实来源 | 报告侧处理 |
|---|---|---|
| Session | `ReportSnapshot.frozen_session` | 读取身份、合同、环境和已冻结 revision；Store 只由 R0A Snapshot adapter 读取 |
| Attempt | `ReportSnapshot.frozen_attempts` | 保留 Snapshot 时刻的运行状态和 retry lineage；`execution_result_ref` 必须唯一解析到 `source_refs` 中已校验的 `ArtifactReferenceV2`，执行结果和 metrics 只通过这些冻结引用读取 |
| Outcome / 执行协议 | `OutcomeCard` | 直接读取执行状态、协议状态和原始执行事实，不重新计算 |
| 科学比较 | `EffectiveScientificAssessment` | 唯一读取比较性、科学效果和 delta 的决策视图 |
| 旧 validity | `ScientificValidityReport` | 仅通过 legacy adapter 读取，不与新 Experiment Agents 事实混为一谈 |
| IdeaTree | `ReportSnapshot.frozen_idea_tree` | 读取 ideas、状态、parent/child、attempt refs、evidence refs 和 insights |
| Candidate/Champion | `ReportSnapshot.frozen_champion_pointer` 及其中的不可变引用 | 读取已冻结的候选和 Champion 指针 |
| 认知成本 | `ReportSnapshot.frozen_cognitive_cost_summary` + `cognitive_usage_sha256` | 读取同一账本窗口生成的 Snapshot 时刻 LLM 调用、token、认知 wall time 和认知预算；fingerprint 只作为该摘要的绑定证据 |
| 计算资源 | `ResourceUsageReport` 及现有资源聚合 | 读取 GPU 数量、显存、利用率、实验 wall time 和 GPU-hours |
| 停止事实 | `ReportSnapshot.frozen_stop_decision` | 读取已冻结的停止原因，不由 assembler 推断 |
| Artifact | `ReportSnapshot.source_refs` 中的 `ArtifactReferenceV2` | 保存带 SHA 的类型化引用 |

R0A 是唯一可以读取 Session、IdeaTree、Attempt、Candidate/Champion、StopDecision 和 CognitiveCostSummary live 来源并写入冻结副本的边界。R1 及后续阶段只接受 Snapshot 和其中登记的不可变引用；即使生成期间控制面继续变化，也不能回读最新值来“补齐” Facts。

Facts assembler 对 `execution_result_ref` 做确定性绑定校验：引用为空、缺失、对应多个 artifact 或 SHA 不匹配时，保留失败/缺失事实和原因，不从路径名、文件扩展名或自由文本猜测执行结果。

当前仓库确实存在 `IdeaTreeStore`，必须纳入 Snapshot 和 Facts。计划中不使用不存在的 `ChampionStore` 或含义不清的通用 `CostSummary`；如果某个事实在当前仓库没有权威来源，输出为缺失/未确定并记录原因，不自行补齐。

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
cognitive_cost_summary
compute_resource_summary
uncertainties
source_refs
```

Facts 中的 `execution_status`、`protocol_intact` 等执行/协议值来自 `OutcomeCard`；`scientific_effect`、`evaluation_status`、`primary_delta` 等比较值只能来自 `EffectiveScientificAssessment`。Assembler 不把“提升”“无效”“建议继续”等自然语言结论写入事实字段。

Ideas 和 Attempts 必须从 Snapshot 中冻结的 `IdeaTree`、`frozen_attempts` 装配，不能回读创建报告之后已经变化的 live 对象。`DRAFT`、`REVIEWED`、`READY`、`RUNNING`、`SUPPORTED`、`NOT_SUPPORTED`、`INCONCLUSIVE`、`PRUNED`、`MERGED` 及其 child relationships 都保留真实状态和 evidence，不因为报告只展示成功结果而丢弃。

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

Arbor 的 partial report 只作为“缺失数据仍能产生可读结果”的参考；AutoAD 的 EvaluationContract、OutcomeCard 和 `EffectiveScientificAssessment` 语义仍是权威。

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

## 8. Hash 和写入

Facts 的 canonical 内容 hash 排除 `generated_at`、`updated_at` 等 volatile 字段；实际写出的文件另算 artifact hash。输出采用 AutoAD 现有原子写模式，不能覆盖已经冻结的事实文件。

## 9. 验收

- [ ] 相同 Snapshot 输入得到相同 Facts content hash。
- [ ] 失败、超时、OOM、不可比较 Attempt 都保留。
- [ ] NON_COMPARABLE Attempt 的 delta 为 `null`。
- [ ] 所有 Facts 中的关键事实都能解析到 Evidence。
- [ ] Evidence 不能引用 Snapshot 外的 artifact。
- [ ] Champion 使用 `CandidateRegistry` / `ChampionPointer` 的真实数据。
- [ ] IdeaTree 的 PRUNED、NOT_SUPPORTED、INCONCLUSIVE 和 child 节点均可进入 Facts。
- [ ] OutcomeCard 与 ScientificAssessment 不一致时，Facts 只暴露 EffectiveScientificAssessment 的比较结果，同时保留两类 evidence refs。
- [ ] CognitiveCostSummary 与 ResourceUsageReport 分开装配，不能互相填充字段。
- [ ] 缺失事实显式标记，不通过默认字符串或示例数值补齐。
- [ ] 输出文件原子写入，失败不留下半截 JSON。

## 10. 不做什么

- 不调用 LLM。
- 不读取整个 stdout/stderr 作为 Facts。
- 不从日志正则推导科学结论。
- 不在 Assembler 中重新组合 OutcomeCard 和 ScientificAssessment 形成第二个“有效评估”事实源。
- 不修改 OutcomeCard、ScientificAssessment 或现有控制面事实。
- 不直接生成“推荐下一步”；建议留给 Narrative/Discussion，并必须建立在 Facts 上。
