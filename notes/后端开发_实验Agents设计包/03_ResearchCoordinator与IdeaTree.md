# 开发计划 02：Research Coordinator 与 Idea Tree

## 1. 目标

实现一个在 ExperimentSession 内持久存在的 Research Coordinator，并使 ideation 建立在累积实验状态上持续发生。

核心循环：

```text
OBSERVE
→ COMPARE
→ REFLECT
→ REVISE HYPOTHESIS
→ IDEATE
→ SELECT
→ PROPOSE DISPATCH
→ DECIDE
```

---

## 2. 设计约束

- Coordinator 可以持久；
- ExperimentSession、Idea Tree、Attempt、CognitiveCommit 才是权威状态；
- LLM messages 是可压缩工作记忆；
- 每个 subprocess 完成不一定触发完整 ReAct；
- 每个 Cognitive Decision Boundary 必须写 CognitiveCommit；
- ideation 中间草稿在提交后裁剪；
- 旧 insight 不覆盖，只追加 reinterpretation。

---

## 3. 实现组件

### 3.1 IdeaTree schema/store

建议文件：

```text
experiment_agents/ideas/models.py
experiment_agents/ideas/store.py
experiment_agents/ideas/tools.py
```

操作工具：

```text
tree_view
tree_add_node
tree_attach_attempt
tree_append_evidence
tree_append_cognitive_commit
tree_request_prune
tree_mark_status
tree_frontier
tree_search
```

mutation 要求：

- expected revision；
- idempotency key；
- schema validation；
- atomic write；
- event；
- revision increment。

### 3.2 CognitiveCommitLedger

新增：

```text
cognition/models.py
cognition/commit_store.py
```

每次认知决策保存：

```text
input outcome refs
observation
comparison
verdict
KEEP-WHY
failure-WHY
confidence
uncertainty
tree mutations
next action
prompt/model version
```

不可修改，只能追加。

### 3.3 Coordinator Agent 配置

通过 `create_deep_agent()`：

- Persistent/checkpoint；
- SummarizationMiddleware；
- 文件权限仅限 Session artifacts；
- 无任意 shell；
- 只能通过受控工具修改 Idea Tree；
- output 最终必须符合 `CycleDecision`。

### 3.4 Coordinator Tool Contract

> **Coordinator 工具是 ResearchCoordinator 与 IdeaTreeStore、AttemptStore、ExperimentWorker、
> DecisionEngine、Git/Worktree 之间的接口合同。必须提前定义。**

```text
工具分类：
  read-only（可并行调用）: tree_view, compare_attempts, record_finding
  mutation（串行，写状态）: tree_add_node, tree_update_node, tree_prune, tree_propagate
  execution（创建子任务）: run_executor, merge_candidate
  HITL（人工介入）: request_user_decision
```

| 工具 | 输入 | 类型 | 安全门槛 |
|------|------|------|----------|
| `tree_view` | `format`（compact/full/node/pending/constraints）, `node_id?` | 只读 | 限制输出大小；node_id 存在 |
| `tree_add_node` | `parent_id`, `mechanism`, `hypothesis`, `observable`, `grounding`, `expected_cost` | 变异 | parent 存在；深度 ≤ 3；禁止重复；预算允许 |
| `tree_update_node` | `node_id`, `status?`, `insight?`, `evidence_refs?` | 变异 | node 存在；运行后的 hypothesis/mechanism 不可原地改写 |
| `tree_prune` | `node_id`, `reason` | 变异 | reason 必填；不能 prune root、running node、champion |
| `tree_propagate` | `node_id` | 变异 | node 已有有效结果；只向祖先追加 insight |
| `run_executor` | `node_id`, `attempt_spec`, `skip_eval=false` | 执行 | node READY；无活跃 Attempt；预算通过；EvaluationContract + protected_hashes 已冻结 |
| `compare_attempts` | `candidate_attempt_id`, `baseline_attempt_id?` | 只读 | 两者均为 SCIENTIFICALLY_EVALUABLE；EvaluationContract hash 一致 |
| `merge_candidate` | `node_id`, `source_branch` | 变异 | 禁止 main/master；B_test 已通过；protected hash 未变；trunk clean |
| `record_finding` | `kind`, `about`, `note`, `evidence_refs` | 追加 | append-only；引用必须存在 |
| `request_user_decision` | `question`, `options`, `reason` | HITL | 仅审批模式/预算冲突/无法自动解决的歧义可调用 |

**两道硬规则（不由 Agent 控制）：**

```text
run_executor:
  执行前检查 protected_hashes.json
  执行后再次检查
  不匹配 → PROTOCOL_VIOLATED

merge_candidate:
  不能由 Coordinator 直接 git merge
  必须由确定性 MergeService 执行
  强制重新跑 B_test
  通过后才合并
```

**Agent 工具只是"提出请求"。** 真正的状态变更由后端的 Store/Service 完成。

### 3.5 Compact Cycle

输入：

```text
SessionSummary
FrontierView
OutcomeCard(s)
ChampionSummary
RecentCognitiveCommits
DeadEndSummary
NoiseFloor
BudgetSnapshot
```

一次 LLM 返回：

```text
CycleDecision
```

### 3.6 Exploratory Cycle

允许调用：

- IdeaExplorerAgent；
- ReflectionAgent；
- repo/source search 工具；
- TreeSearch；
- 更高 token/step budget。

触发器：

```text
conflict
stagnation
low confidence
large pivot
high-value result
novel literature needed
```

### 3.7 IdeaExplorerAgent

临时运行，输入累积状态包，输出多个差异化候选：

```text
mechanism
hypothesis
observable
research_axis
minimal intervention
falsification
expected cost
relationship to previous ideas
```

---

## 4. Context Pruning

在 Idea 提交和 CognitiveCommit 成功后：

1. 删除 ideation scratch；
2. 截断大 tool output；
3. 保留最终 idea 与 evidence refs；
4. 保留最近决策；
5. 超阈值时摘要；
6. 恢复时优先读取 TreeView 和 commits。

需要记录：

```text
prune_event
before_tokens
after_tokens
preserved_refs
summary_hash
```

## 4.1 Coordinator 崩溃恢复：ObservationSnapshot + CognitiveCommit

采用 Arbor 的简化做法：Idea Tree 是权威，Snapshot 是草稿，Commit 是正式结果，DeepAgents checkpoint 是加速恢复缓存。

### ObservationSnapshot（OBSERVE 后写）

OBSERVE 完成后写：

```text
cycle_id
tree_revision
outcome_refs
observation
ideation_focus
```

它不是科研结论——只用于快速恢复时跳过重复 OBSERVE。

### CognitiveCommit（完整决策后）

OBSERVE + IDEATE + SELECT + validated mutations + next action 全部完成后才写。

### 崩溃恢复规则（简化为两条）

```text
ObservationSnapshot 存在 AND tree_revision 未变 → 重做 IDEATE
ObservationSnapshot 丢失 OR tree_revision 已变 → 从 Tree + Attempts 重做 OBSERVE
有 CognitiveCommit 但 Job 未创建 → 以 commit_id 为幂等键补建 Job
```

不需要 8 态状态机。Tree 是权威，Snapshot 是草稿，checkpoint 是缓存。

---

## 5. 认知成本策略

新增 `CognitiveBudget`：

```text
max_calls
max_tokens
max_compact_cycles
max_exploratory_cycles
max_subagent_calls
max_wall_seconds
```

模式：

### cheap experiment

- Coordinator 一次生成小批次；
- 运行 2–4 个 variants；
- batch 完成后一次 Compact Cycle。

### expensive experiment

- 每个有效结果后 Compact Cycle；
- 高风险时允许 Exploratory Cycle。

---

## 6. 开发步骤

### PR 02A：Idea Tree 与 CognitiveCommit

无 LLM，先完成：

- schema；
- store；
- mutation；
- revision；
- event；
- recovery；
- immutable commit。

### PR 02B：Coordinator AgentFactory

- role config；
- tool profile；
- permissions；
- output schema；
- checkpoint；
- mock model。

### PR 02C：Compact Cycle

- deterministic ContextPack；
- 单次 structured output；
- mutation application；
- post-commit pruning。

### PR 02D：Exploratory Cycle 与 IdeaExplorer

- trigger；
- subagent；
- multiple candidates；
- budget；
- fallback。

### PR 02E：Recovery

- checkpoint 存在；
- checkpoint 缺失；
- checkpoint 与 Tree revision 不一致；
- pending attempt 重连。

---

## 7. 检验方案

### 7.1 Idea Tree 单元测试

- concurrent revision conflict；
- duplicate mutation 幂等；
- child depth；
- status transition；
- old commit 不可覆盖；
- append reinterpretation；
- atomic save。

### 7.2 Coordinator 合同测试

使用固定模型输出：

1. 清晰改善 → 派生 child；
2. 噪声内变化 → 补 seed；
3. implementation invalid → repair；
4. budget critical → stop；
5. duplicate idea → validator 拒绝；
6. forbidden direction → 拒绝。

### 7.3 连续 ideation 测试

构造三轮 fixture：

```text
attempt 1: improvement
attempt 2: regression
attempt 3: category conflict
```

验证：

- 第 2、3 轮 idea 使用前轮 KEEP-WHY / failure-WHY；
- 不重复已失败的完全相同 intervention；
- 每轮有新的 CognitiveCommit；
- Idea Tree 能表达父子关系。

### 7.4 Pruning 测试

- 10 轮长工具输出；
- context 被裁剪；
- Idea Tree 和 commits 无损；
- 新 Coordinator 可仅凭外部状态恢复并做出一致动作类别。

### 7.5 成本测试

记录：

- 每 cycle calls；
- tokens；
- compact/exploratory 比例；
- cheap batch 下认知 wall time；
- 认知成本不随原始日志线性增长。

### 7.6 验收标准

- Coordinator 常规循环一次 LLM 即可完成；
- ideation 每个 research cycle 连续发生；
- IdeaExplorer 不是唯一 idea 来源；
- context pruning 前必写 commit；
- 删除 checkpoint 后可恢复；
- 10+ cycles 后树、commit 和下一步选择仍可追溯；
- cheap batch 不会每个 subprocess 都触发深循环。
