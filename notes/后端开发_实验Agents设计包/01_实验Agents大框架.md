# AutoAD 实验 Agents 大框架

> 文档状态：架构基线 v1  
> 适用范围：AutoAD-Researcher 的中间实验自迭代系统  
> 明确不包含：前端意图对齐、用户对话路由、最终报告生成与展示

---

## 1. 目标

实验 Agents 系统接收已经确认的实验任务与代码仓库上下文，在受控环境中持续执行：

```text
理解当前研究状态
→ 提出或修正假设
→ 选择下一项实验
→ 修改代码
→ 运行实验
→ 验证结果是否可信
→ 对比历史实验
→ 更新研究认知
→ 继续、补实验、派生、剪枝或停止
```

系统不是一次性 pipeline，也不是让 LLM 自由控制机器。

核心原则：

> Persistent cognition, externalized state, ephemeral execution, deterministic governance.

对应中文：

- 研究认知可以持续；
- 权威状态必须外部持久化；
- 实现与实验执行单元按任务创建；
- 环境、资源、安全、有效性和停止规则由确定性代码治理。

---

## 2. 参考项目吸收结论

### 2.1 直接吸收的确定性模式

| 模式 | 参考来源 | AutoAD 落点 |
|---|---|---|
| protected artifact SHA256 guard | AutoSOTA | EvaluationContract / ProtectedArtifactGuard |
| NaN fast fail | autoresearch | 训练脚本适配器 / Sentinel |
| fixed wall-clock budget | autoresearch | AttemptBudget |
| stable step signature | MiMo/OpenCode | Job idempotency / ToolCall signature |
| step/cost/wall 三重限制 | mini-swe-agent | AgentBudget / CognitiveBudget |
| finally-save | mini-swe-agent | trajectory、attempt、checkpoint 强制落盘 |
| StuckDetector | software-agent-sdk | Coordinator / Executor 防重复与卡死 |
| shell=False | AutoAD 现有 Environment / Runner | 所有命令执行 |
| git worktree | Arbor | 每个代码实验分支 |
| B_dev/B_test | Arbor | 研究选择与最终验证分离 |
| noise floor | AutoScientists | ScientificEffect 判断 |
| correctness gate | AutoLab | Implementation/Evaluation validity |

### 2.2 直接吸收的认知模式

| 模式 | 来源 | AutoAD 落点 |
|---|---|---|
| OBSERVE→IDEATE→SELECT→DISPATCH→DECIDE | Arbor | Research Coordinator |
| KEEP-WHY | AutoScientists | Reflection / CognitiveCommit |
| 由成功属性衍生候选 | AutoScientists / Arbor | 下一轮 IDEATE |
| Structured critique JSON | AutoFigure | ReflectionResult |
| best-result tracking | AutoFigure / autoresearch | ChampionStore |
| “I am done” sentinel | AI-Scientist | Coordinator Stop Proposal |
| FORBIDDEN / PREFERRED directions | AutoSOTA | Session ResearchPrior |
| prompt + code + archive sandwich | AI-Scientist | Coordinator ContextPack |
| 子 Agent = 同一引擎换配置 | DeepAgents / Claude Code 模式 | AgentFactory |

### 2.3 不直接复制的部分

- 不复制外部项目的完整源码仓，但**直接复用已验证的组件代码**（如 aider 的 SEARCH/REPLACE 策略栈、SWE-Together 的 InfraFailureSentinel、MiMo 的 stableStringify）；
- 不引入第二套任务队列；
- 不将 GPU 进程生命周期交给 Agent；
- 不使用固定 20+ Stage 的大管线；
- 不把 LLM messages 当作实验状态数据库；
- 不要求程序完全判定“代码 bug”与“科学假设失败”。

---

## 3. 系统总览

```text
┌───────────────────────────────────────────────────────────────┐
│ Experiment Entry                                              │
│ 已确认的 input_task / repository evidence / execution policy  │
└─────────────────────────────┬─────────────────────────────────┘
                              ▼
┌───────────────────────────────────────────────────────────────┐
│ Experiment Control Plane                                      │
│                                                               │
│ ExperimentSessionStore                                        │
│ ExperimentCoordinatorService                                  │
│ JobStore / AttemptStore / EventLog / ArtifactStore            │
│ BudgetPolicy / ApprovalPolicy / StopPolicy                    │
└───────────────┬───────────────────────────────┬───────────────┘
                │ cognition                     │ durable jobs
                ▼                               ▼
┌───────────────────────────────┐   ┌───────────────────────────┐
│ Persistent Research Agent     │   │ Execution Plane           │
│                               │   │                           │
│ ResearchCoordinator           │   │ EnvironmentWorker         │
│ - observe                     │   │ ExperimentWorker          │
│ - compare                     │   │ WorktreeManager           │
│ - reflect                     │   │ GPU ResourceLease         │
│ - revise hypothesis           │   │ Runner + Sentinel         │
│ - ideate                      │   │ Metrics/Resource Collector│
│ - select                      │   │                           │
│ - dispatch proposal           │   │                           │
└──────────────┬────────────────┘   └──────────────┬────────────┘
               │                                    │
               ├── IdeaExplorerAgent                │
               ├── ReviewerAgent                    │
               ├── ExecutorAgent                    │
               ├── ReflectionAgent                  │
               └── HealthDiagnosisAgent             │
                              ┌──────────────────────┘
                              ▼
┌───────────────────────────────────────────────────────────────┐
│ Scientific Validity and Memory                                │
│                                                               │
│ EvaluationContract / InterventionContract                     │
│ IdeaTree / CognitiveCommitLedger / ChampionStore              │
│ NoiseFloor / ValidityGate / DecisionEngine                    │
│ ConvergenceMonitor / StrategyOverlay                          │
│ BatchSupervisor / BatchFailurePolicy                          │
│ PreApplyPatchGate / PostApplyDiffGuard                        │
│ NoiseCalibrationPolicy / LaunchProfile                        │
│ CycleJournal / ObservationSnapshot                            │
└───────────────────────────────────────────────────────────────┘
```

---

## 4. 生命周期与权威状态

### 4.1 ExperimentSession

一个 Session 对应一组稳定研究边界：

```text
session_id
task_ref
repository_ref
baseline_commit
objective
allowed_change_scope
forbidden_change_scope
evaluation_contract
environment_snapshot_ref
budget
status
current_champion
idea_tree_revision
prompt_profile_version
```

Session 是实验契约和生命周期的权威来源。

### 4.2 Idea Tree

Idea Tree 是科研搜索和累积认知的权威来源，不是 LLM 对话历史。

节点建议：

```text
idea_id
parent_id
depth
research_axis
mechanism
hypothesis
observable
intervention_contract_ref
status
attempt_refs
evidence_refs
cognitive_commit_refs
children
created_at
```

建议状态：

```text
DRAFT
REVIEWED
READY
RUNNING
SUPPORTED
NOT_SUPPORTED
INCONCLUSIVE
PRUNED
MERGED
```

### 4.3 CognitiveCommitLedger

每个认知决策边界必须写入不可变 CognitiveCommit：

```text
observation
comparison
hypothesis_verdict
keep_why
failure_why
mechanism_interpretation
confidence
uncertainty
next_action
evidence_refs
model_profile
prompt_version
```

旧 commit 不覆盖。后续重新解释通过新 commit 追加。

### 4.4 AttemptStore

Attempt 是真实执行事实：

```text
attempt_id
idea_id
worktree_ref
environment_snapshot_ref
resource_lease_ref
patch_ref
command_plan_ref
execution_status
implementation_status
evaluation_status
scientific_effect
metrics_ref
resource_ref
failure_ref
```

### 4.5 LLM checkpoint

DeepAgents checkpoint 只是连续认知缓存：

- 可以摘要；
- 可以裁剪；
- 可以丢失；
- 可以从 Session、Idea Tree、Attempt 和 CognitiveCommit 重建。

### 4.6 CycleJournal 与 ObservationSnapshot — Coordinator 崩溃恢复

Coordinator 的认知周期使用两阶段记录，而非每次 OBSERVE 后写完整 CognitiveCommit。

**CycleJournal（运行恢复）：**

```text
状态：CREATED | OBSERVING | OBSERVED | IDEATING | PROPOSED | COMMITTING | COMMITTED | DISPATCHED
```

OBSERVE 完成后写 **ObservationSnapshot**：

```text
当前 Tree revision
OutcomeCard 引用
关键比较结果
未解决问题
下一步 ideation focus
prompt/context hash
```

ObservationSnapshot 只是恢复检查点，不是科研结论。

**CognitiveCommit（完整决策）：** 只有 OBSERVE + IDEATE + SELECT + validated mutations + next action 全部完成后才写。

**崩溃恢复规则：**

| 崩溃位置 | 恢复行为 |
|----------|----------|
| OBSERVED 前 | 重做 OBSERVE |
| OBSERVED 后、PROPOSED 前（Tree 未变化） | 只重做 IDEATE |
| PROPOSED 后、COMMITTED 前（Tree 未变化） | 验证 proposal 后幂等提交 |
| COMMITTED 后、DISPATCHED 前 | 只补建 Job，不重新生成 Idea |
| Tree revision 已变化 | 废弃旧 ObservationSnapshot，重新 OBSERVE |

---

## 5. Research Coordinator

### 5.1 定位

Research Coordinator 是一个在 ExperimentSession 内持久运行的 DeepAgents Agent。

它不是单纯调度器，而是主研究者：

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

### 5.2 每个 experiment 是否都调用完整循环

不是。

系统区分：

#### Result Integration Event

每个实验完成后由确定性代码执行：

- 解析 execution result；
- 验证 artifact；
- 计算 delta；
- 应用 noise floor；
- 更新 Attempt；
- 生成 OutcomeCard。

不调用 LLM。

#### Cognitive Decision Boundary

只有需要选择下一步时调用 Coordinator：

- 一个顺序实验完成；
- 一个便宜实验批次完成；
- 出现矛盾结果；
- 需要补 seed；
- 需要判断 repair / new hypothesis；
- 预算、收敛或高风险 gate 触发。

### 5.3 Compact / Exploratory 两级循环

#### Compact Cycle

默认一次结构化 LLM 调用，完成：

```text
compare + reflect + hypothesis update + ideate + next action
```

用于结果清晰、实验便宜、当前方向稳定、不需要查资料或深入读代码的情况。

#### Exploratory Cycle

完整 DeepAgents ReAct，用于：

- 停滞；
- 冲突证据；
- 大范围 pivot；
- 需要多方向搜索；
- 需要文献或代码深读；
- Coordinator 置信度低。

### 5.4 Ideation 连续性

正常 ideation 由 Coordinator 每个认知周期执行。

IdeaExplorerAgent 只是深度推理放大器，按需提供：

- 多研究轴发散；
- 文献机制扩展；
- 失败方向重组；
- 认知疲劳或重复 idea 修正。

临时 Agent 实例不等于一次性研究记忆。它读取累积状态包，并将结果写回 Idea Tree。

### 5.5 上下文控制

idea 提交并写入 CognitiveCommit 后：

- 删除 brainstorm 草稿；
- 裁剪大 tool outputs；
- 保留最终观察、结论、idea 与证据引用；
- 超限时使用 DeepAgents SummarizationMiddleware；
- Idea Tree 和 CognitiveCommit 不受摘要影响。

---

## 6. Agent 角色

| 组件 | 生命周期 | 主要职责 | 不负责 |
|---|---|---|---|
| ResearchCoordinator | Session 内持久 | 连续研究决策与 ideation | GPU 进程、直接改权威状态文件 |
| IdeaExplorerAgent | 按需临时 | 深度发散、文献驱动 ideation | 常规每轮决策 |
| ReviewerAgent | 高成本/高风险时临时 | 假设可证伪性、重复性、泄漏和成本评审 | 硬权限校验 |
| ExecutorAgent | 每个 attempt 临时；attempt 内可保留短期上下文 | 修改代码、有界修复、实现日志 | 长训练监控、最终科学裁决 |
| ReflectionAgent | 矛盾或高价值结果时临时 | KEEP-WHY、失败机制、衍生假设 | 指标真实性验证 |
| HealthDiagnosisAgent | 未知异常事件触发 | 对压缩健康证据作语义诊断 | 周期性实时盯训练 |
| StrategyDiagnosticAgent | 停滞事件触发 | 解释探索停滞、提出策略 overlay | 直接改基础 prompt |
| ConvergenceMonitor | 持续确定性代码 | 无提升、重复率、预算、KEEP 率 | 科学原因解释 |

所有 LLM Agent 均通过统一 `create_deep_agent(config)` 工厂创建，但使用不同：

- system prompt；
- tools；
- filesystem backend；
- permissions；
- middleware；
- output schema；
- token/cost/wall limits。

### 6.1 CognitiveTaskRunner — 隔离 DeepAgents 依赖

Coordinator 不直接调用 `create_deep_agent()`，而是通过一层薄接口：

```python
class CognitiveTaskRunner(Protocol):
    """薄接口——隔离业务逻辑与 DeepAgents 框架。"""
    def invoke(self, spec: AgentTaskSpec) -> AgentTaskResult: ...

@dataclass
class AgentTaskSpec:
    role: str                    # "idea_explorer" | "reviewer" | "executor" | "reflection"
    prompt_profile: str          # prompt template name
    input_artifact_refs: list[str]  # artifact paths 引用
    output_schema: type[BaseModel]
    tool_profile: list[str]
    permission_profile: str
    model_profile: str
    token_budget: int
    wall_time_budget_sec: int
    trace_context: dict
```

然后：

```text
DeepAgentsTaskRunner  ← 负责将 AgentTaskSpec 翻译为 create_deep_agent(...) 的配置
MockTaskRunner         ← 测试用，不调 LLM
```

业务模块只知道：

```text
请执行 idea_exploration 认知任务
请执行 code_implementation 认知任务
请执行 result_reflection 认知任务
```

而不知道 DeepAgents 的内部实现。好处：
- 测试时可以 mock；
- 换模型/框架时只改 runner；
- 统一 tracing；
- 每个 Agent 的 config 都在 spec 里声明，可审计；
- 不扩展角色方法式大 ABC。

---

## 7. 环境准备

现有 Environment 子系统继续复用：

```text
EnvironmentPlan
→ Policy
→ Adapter
→ Builder
→ Validation
→ Snapshot
→ bounded revision
```

需要补齐：

1. ExperimentSession 接线；
2. `experiment_environment_prepare` Job；
3. RepositoryProbe / HostProbe；
4. ValidationContextCollector；
5. 真实 GPU compute probe；
6. observed EnvironmentSnapshot；
7. 环境失败的结构化诊断与最多两次修订。

环境、代码、GPU 三种隔离必须分开：

| 系统 | 隔离对象 |
|---|---|
| venv / conda | 依赖 |
| git worktree | 代码修改 |
| ResourceLease | GPU 动态占用 |

---

## 8. 代码实现与有界修复

### 8.1 InterventionContract

每个 Idea 执行前冻结：

```text
scientific mechanism
allowed files/modules
allowed parameters
protected files
expected activation evidence
evaluation invariants
max repair count
```

### 8.2 ExecutorAgent

采用 SEARCH/REPLACE，最多进行有限修复，例如 3 次。

允许在同一 Attempt 内修复：

- syntax/import；
- shape；
- 参数传递；
- hook 未激活；
- parser；
- smoke test；
- 明确的 OOM 配置缩减。

如果需要改变科学机制或越出 InterventionContract，返回 `SEMANTIC_DEVIATION`，由 Coordinator 决定是否创建 child Idea。

---

## 9. Experiment Job、GPU 与训练监控

### 9.1 Durable Job

长任务必须由持久 Job 执行，不使用进程内 background task。

建议初始 Job 类型：

```text
experiment_environment_prepare
experiment_baseline
experiment_attempt
experiment_confirmatory
```

### 9.2 ResourceLease

每个 GPU Attempt 动态申请：

```text
lease_id
worker_id
attempt_id
device_ids
required_vram
allocated_at
expires_at
heartbeat_at
```

GPU 编号不写死在 EnvironmentSnapshot。

### 9.3 训练三层监控

#### 训练脚本内

- metric progress；
- best checkpoint；
- NaN/Inf fast fail；
- early stop；
- heartbeat；
- SIGTERM 保存。

#### Sentinel

- PID；
- heartbeat stale；
- wall timeout；
- GPU process；
- disk；
- OOM/NaN pattern；
- expected outputs；
- TERM→KILL。

#### HealthDiagnosisAgent

只在 Sentinel 无法分类时事件触发，输出建议，不直接 kill。

---

## 10. 实验有效性

不能直接使用“产生指标 = idea 已有效评估”。

必须通过四层状态：

### Execution

```text
COMPLETED / CRASHED / TIMEOUT / CANCELLED
```

### Implementation

```text
VERIFIED / UNVERIFIED / INVALID
```

需要确认目标代码路径和干预实际生效。

### Evaluation

```text
COMPARABLE / NON_COMPARABLE
```

检查 split、metric、seed、checkpoint 和 evaluation contract。

### Scientific Effect

```text
IMPROVEMENT
NO_EFFECT
REGRESSION
INCONCLUSIVE
```

只有 `COMPLETED + VERIFIED + COMPARABLE` 才进入 Scientific Effect。

单次回归通常只能说明当前具体实现未支持该假设，不能直接证明整个科学方向被反驳。

---

## 11. Reflection、Decision 与 Champion

### 11.1 默认确定性处理

- metric delta；
- noise floor；
- resource guardrail；
- validity；
- champion ranking；
- confirmatory seed requirement。

### 11.2 Coordinator 基础反思

每个认知决策边界执行：

- hypothesis verdict；
- KEEP-WHY；
- failure reason；
- uncertainty；
- next action。

### 11.3 深度 ReflectionAgent

只处理：

- seed 冲突；
- 指标间冲突；
- 类别表现相反；
- 机制与结果不一致；
- 高价值成功；
- 疑似实现偏差。

### 11.4 决策

```text
PROMOTE_CANDIDATE
CONFIRM_WITH_MORE_SEEDS
REFINE_IMPLEMENTATION
DERIVE_CHILD_IDEA
CONTINUE_BATCH
PIVOT
PRUNE
STOP
```

B_dev 用于探索，B_test 只用于 champion 合并或最终确认。

---

## 12. 收敛与策略调整

ConvergenceMonitor 计算：

- 连续无提升；
- 最近窗口 KEEP 率；
- idea 语义重复率；
- research axis 集中度；
- implementation failure rate；
- invalid attempt rate；
- budget burn；
- champion stagnation。

StrategyDiagnosticAgent 输出建议。

策略变更由确定性 StrategyPolicy 应用为 `session-scoped prompt overlay`，要求：

- 只追加；
- 不改 objective/evaluation/protected rules；
- 版本化；
- 记录来源；
- 可回滚；
- 可设置 TTL；
- 写 `prompt_changes.jsonl`。

---

## 13. 认知与计算预算

系统同时管理：

### ComputeBudget

```text
GPU hours
attempt count
wall time
VRAM
storage
```

### CognitiveBudget

```text
LLM calls
input/output tokens
deep cycles
IdeaExplorer calls
Reflection calls
Reviewer calls
```

便宜实验使用批处理和 Compact Cycle；昂贵实验使用顺序模式并允许更深推理。

每个 Session 必须输出：

```text
compute_cost
cognitive_cost
wall_clock_cost
```

---

## 14. Artifact 目录建议

```text
runs/<run_id>/experiment/<session_id>/
├── session.json
├── evaluation_contract.json
├── environment/
│   ├── plan.json
│   ├── policy_report.json
│   ├── build_result.json
│   ├── validation_context.json
│   ├── validation_report.json
│   └── snapshot.json
├── ideas/
│   ├── tree.json
│   ├── tree.md
│   └── cognitive_commits.jsonl
├── strategy/
│   └── prompt_changes.jsonl
├── attempts/
│   └── <attempt_id>/
│       ├── intervention_contract.json
│       ├── workspace.json
│       ├── patch.diff
│       ├── repair_log.jsonl
│       ├── command_plan.json
│       ├── execution_result.json
│       ├── heartbeat.json
│       ├── stdout.log
│       ├── stderr.log
│       ├── metrics.json
│       ├── resource_usage.json
│       ├── validity.json
│       ├── outcome_card.json
│       └── reflection.json
├── champion.json
├── jobs.jsonl
├── events.jsonl
└── trajectory.jsonl
```

---

## 15. 第一版开发边界

第一版必须具备：

- V2→实验接线（PR-001A）
- Session；
- Environment 接线和真实 probe；
- baseline；
- worktree；
- ExecutorAgent；
- Experiment Job；
- timeout / heartbeat / Sentinel；
- protected hash；
- implementation activation evidence；
- comparable evaluation；
- noise-aware result；
- 扁平但可演化的 Idea Tree；
- 持久 Research Coordinator；
- Compact Cycle；
- CognitiveCommit；
- 至少两轮真实迭代。

第一版不要求：

- 多机调度；
- K8s；
- 自动 Docker 构建；
- 复杂树搜索算法；
- 多团队 Agent；
- 跨 Session 自动 prompt 自修改；
- 自动论文和最终报告 Agent；
- 前端实验交互。
