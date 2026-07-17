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

### 2.1 参考的确定性模式

| 模式 | 参考来源 | AutoAD 落点 | 复用等级 |
|------|----------|------------|---------|
| protected artifact SHA256 guard | AutoSOTA | EvaluationContract / ProtectedArtifactGuard | `[REIMPL]` |
| NaN fast fail | autoresearch | 训练脚本适配器 / PostRunFailureClassifier | `[REFER]` |
| fixed wall-clock budget | autoresearch | AttemptBudget | `[REFER]` |
| stable step signature | MiMo/OpenCode | Job idempotency / ToolCall signature | `[PORT-PENDING-LICENSE]` |
| step/cost/wall 三重限制 | mini-swe-agent | AgentBudget / CognitiveBudget | `[REFER]` |
| finally-save | mini-swe-agent | trajectory、attempt、checkpoint 强制落盘 | `[REFER]` |
| StuckDetector | software-agent-sdk | Coordinator / Executor 防重复与卡死 | `[ADAPT-LATER]` |
| shell=False | AutoAD 现有 Environment / Runner | 所有命令执行 | — |
| git worktree | Arbor | 每个代码实验分支 | `[REFER]` |
| B_dev/B_test | Arbor | 研究选择与最终验证分离 | `[REFER]` |
| noise floor | AutoScientists | ScientificEffect 判断 | `[REFER]` |
| correctness gate | AutoLab | Implementation/Evaluation validity | `[REFER]` |

### 2.2 参考的认知模式

| 模式 | 来源 | AutoAD 落点 | 复用等级 |
|------|------|------------|---------|
| OBSERVE→IDEATE→SELECT→DISPATCH→DECIDE | Arbor | Research Coordinator | `[REFER]` |
| KEEP-WHY | AutoScientists | Reflection / CognitiveCommit | `[REFER]` |
| 由成功属性衍生候选 | AutoScientists / Arbor | 下一轮 IDEATE | `[REFER]` |
| Structured critique JSON | AutoFigure | ReflectionResult | `[REFER]` |
| best-result tracking | AutoFigure / autoresearch | ChampionStore | `[REFER]` |
| "I am done" sentinel | AI-Scientist | Coordinator Stop Proposal | `[REFER]` |
| FORBIDDEN / PREFERRED directions | AutoSOTA | Session ResearchPrior | `[REFER]` |
| prompt + code + archive sandwich | AI-Scientist | Coordinator ContextPack | `[REFER]` |
| 子 Agent = 同一引擎换配置 | DeepAgents / Claude Code 模式 | AgentFactory | `[REIMPL]` |

### 2.3 复用边界

外部能力按 `[COPY] / [PORT] / [ADAPT] / [REIMPL] / [REFER]` 五级分级，使用前必须在本索引 `00_README.md` 的复用矩阵中完成来源、许可证和复用等级记录。

本轮边界：

- `[COPY]`：SWE-Together `eval_infra_sentinel.py`（Apache-2.0，vendor 并保留 LICENSE/NOTICE）；
- `[REIMPL]`：AutoSOTA SHA256 guard、aider SEARCH/REPLACE 策略；
- `[PORT-PENDING-LICENSE]`：MiMo `stableStringify`；
- `[ADAPT-LATER]`：OpenHands 5-mode StuckDetector；
- `[REIMPL]`：Claude Code internals（工具注册表、权限模型、会话恢复、文件状态缓存）；
- `[REFER]`：Arbor、AI-Scientist、autoresearch、AutoScientists、mini-swe-agent、Anomalib。
- 不复制外部项目的完整源码仓；
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
│ - revise hypothesis           │   │ Runner + RuntimeWatchdog  │
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
│ IdeaTree / CognitiveCommitLedger                              │
│ CandidateRegistry / ChampionEventLog / PromotionJournal        │
│ NoiseFloor / ValidityGate / DecisionEngine                    │
│ ConvergenceMonitor / StrategySelector                          │
│ BatchSupervisor / BatchFailurePolicy                          │
│ PreApplyPatchGate / PostApplyDiffGuard                        │
│ NoiseCalibrationPolicy / LaunchProfile                        │
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
current_champion_pointer  # 指向 CandidateSnapshot.candidate_id
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

### 4.6 ObservationSnapshot — Coordinator 崩溃恢复

采用 Arbor 的简化做法：不引入 8 态状态机。

```text
IdeaTree                科研状态真源
ObservationSnapshot     当前轮临时观察结果 (cycle_id + tree_revision + outcome_refs)
CognitiveCommit         已完成的认知决策
DeepAgents checkpoint   可丢失的对话缓存
```

恢复规则：

```text
ObservationSnapshot 存在 AND tree_revision 未变 → 重做 IDEATE
Snapshot 丢失 OR tree_revision 已变 → 从 Tree + Attempts 重做 OBSERVE
有 CognitiveCommit 但 Job 未创建 → 以 commit_id 补建 Job
```

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

## 6. Agent 角色与组件

运行时拓扑：**1 个持久 ResearchCoordinator + 0～N 个按需 Specialist invocation**。所有 LLM 角色通过同一 `AgentFactory → create_deep_agent()` 创建，不是 6 套独立系统。

参考 Anthropic 多 Agent Research 系统：lead agent 动态创建 subagents，数量取决于当前问题；subagents 独立探索后只向 lead agent 返回压缩结果。研究过程是动态、路径依赖的，不适合固定硬编码流程。

### 6.1 LLM Specialist Profile Catalog（当前定义 6 个）

这 6 个是**当前已识别出的专业能力边界**，不是永远不得增减的组织架构。

| Specialist | 生命周期 | 主要职责 | 独立理由 |
|---|---|---|---|
| **ResearchCoordinator** | Session 持久 | 常规决策、压缩状态 ideation/reflection | 唯一持久决策者，拥有读写科研状态权限 |
| **IdeaExplorerAgent** | 按需临时 | 深度文献/仓库发散 ideation，跨 research axis 探索 | 长上下文（论文/仓库），探索预算独立 |
| **ReviewerAgent** | 高成本/高风险时临时 | 假设可证伪性、重复性、泄漏、成本评审 | **独立第二视角**，避免 Coordinator 自我确认偏差 |
| **ExecutorAgent** | 每个 attempt 临时 | 修改代码、有界修复、实现日志 | 写 worktree 权限，生命周期和失败边界完全不同 |
| **ReflectionAgent** | 矛盾/高价值结果时临时 | KEEP-WHY、失败机制、衍生假设、多 seed 协调 | 指标与机制分析上下文独立 |
| **HealthDiagnosisAgent** | 证据模糊/冲突时触发 | 模糊、未知、新型或矛盾的运行异常根因分析 | 日志/运行证据领域与科研上下文完全不同 |

### 6.2 Coordinator ↔ Specialist 委派模型

Coordinator 完成**常规、压缩、低成本**认知工作；Specialist 处理**会污染主上下文、需要独立视角、需要专用工具或需要更大推理预算**的任务。

**委派不依赖硬规则**（如 `if seed_conflicts >= 2: call_reflection_agent()`），而是 Coordinator 综合以下结构化因素判断：

```text
context_volume          — 当前上下文是否已接近容量上限
task_complexity         — 所需推理链的预计深度
independence_required   — 是否需要独立第二视角（如 review）
specialized_tools_required — 是否需要专用工具（如 repo search）
permission_difference   — 是否需要不同权限边界
uncertainty             — Coordinator 自身置信度
expected_information_gain — 增加这次调用的预期价值
cost                    — 预算约束
```

只有权限和安全边界采用硬规则；是否需要更深认知由 Coordinator 做上下文感知的委派判断。

**典型委派场景：**

| 场景 | Coordinator 做的事 | 委派给 Specialist |
|---|---|---|
| 普通结果 | 直接生成 CycleDecision | — |
| 多 seed / category 冲突 | 读结果摘要 | ReflectionAgent 协调冲突 |
| 普通局部 idea | 直接从 champion 衍生 | — |
| 深读多篇论文和仓库 | 读摘要 | IdeaExplorerAgent 发散探索 |
| 高成本实验提案 | 读 proposal | ReviewerAgent 独立审查 |
| 确定性分类清晰的故障 | 跑 FailurePolicy | — |
| 证据模糊/冲突的运行异常 | 读分类结果 | HealthDiagnosisAgent 根因分析 |

### 6.3 Coordinator Skills（通过 Profile 切换）

这些不是独立 Agent，是 Coordinator 在不同模式下加载的 SKILL.md：

```text
strategy-recovery          → 停滞时，结合 ConvergenceAlert 决定是否切换探索策略
failure-response-planning  → 根据已分类的故障决定科研流程动作（retry / repair / archive / probe / child idea）
batch-review               → 连续低价值实验时批量审视方向
```

> **注意区分：** `failure-response-planning` Skill 做科研流程决策（"这个故障后下一步怎么走"），HealthDiagnosisAgent 做运行根因诊断（"这个故障到底是什么原因"）。两者职责不同，不争夺入口。

### 6.4 Deterministic Components（非 LLM）

| 组件 | 类型 | 职责 |
|---|---|---|
| ConvergenceMonitor | 确定性 Python 组件 | 滑动窗口、velocity、parent exhaustion、收敛信号 |
| RuntimeWatchdog | 确定性运行监控 | PID/process group 存活、stale heartbeat、KILL |
| PostRunFailureClassifier | 规则/证据分类 | 结构化 event → exit code → adapter exception → schema → stderr fallback → UNKNOWN |
| AttemptFinalizer | 确定性写入 | OutcomeCard 综合 |
| DecisionEngine | 确定性 Gate | 指标/guardrail 判定 KEEP/DISCARD |
| PromotionJournal | 持久化事务 | DVC experiment apply + Git merge + Optuna JournalStorage |
| StrategySelector | 确定性过滤排序 | 只过滤和排序当前可用的 strategy skills；不决定使用哪个 skill（由 Coordinator 选择） |
| BudgetPolicy / StopPolicy | 确定性策略 | GPU 小时、attempt 上限、wall time |

> **已删除：StrategyDiagnosticAgent** — 其职责由 ConvergenceMonitor → ConvergenceAlert → Coordinator + StrategySelector 替代。StrategySelector 只过滤排序，Coordinator 根据 alert + 科研历史选择 skill。

**HealthDiagnosisAgent 触发条件**（非硬编码 `failure_code == UNKNOWN`）：
- 分类器 confidence 低
- 多个证据相互冲突
- 已知 FailureCode 与实际状态不一致
- 重复恢复失败
- 出现未覆盖的新型 evidence

流程：
```text
Runtime evidence
→ PostRunFailureClassifier
→ 若分类清楚：FailurePolicy
→ 若证据模糊/冲突：HealthDiagnosisAgent
→ HealthPolicy
```

所有 LLM Agent 均通过统一 `create_deep_agent(config)` 工厂创建，但使用不同：

- system prompt；
- tools；
- filesystem backend；
- permissions；
- middleware；
- output schema；
- token/cost/wall limits。

### 6.4 AgentFactory — 复用 DeepAgents SubAgentMiddleware

**FINAL DISPOSITION: CognitiveTaskRunner 已删除。** 直接使用 DeepAgents 的声明式 Agent 模式：

```python
from deepagents import create_deep_agent, SubAgent

class AgentFactory:
    """声明式 Agent 配置工厂，不新增第二套 TaskRunner 抽象。"""

    @staticmethod
    def create_coordinator_sub_agent(role: str, profile: str) -> SubAgent:
        return SubAgent(
            name=role,
            description=f"AutoAD {role} sub-agent",
            system_prompt=profile,
            tools=["tree_view", "tree_add_node", "tree_prune", "cognitive_ledger_read"],
            middleware=["permissions", "summarization", "budget", "trace"],
        )
```

Coordinator 直接调用 `create_deep_agent()`，不通过中间层：

```text
AgentProfile (声明式配置)
→ AgentFactory (配置工厂)
→ create_deep_agent() (DeepAgents 标准入口)
→ SubAgentMiddleware (权限/摘要/预算/追踪)
```

参考：DeepAgents `create_deep_agent` + `SubAgentMiddleware` + 声明式 `SubAgent` + checkpointer + response_schema。

测试时使用 DeepAgents 内置的 `FakeChatModel` mock，不需要 MockTaskRunner。

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

#### RuntimeWatchdog（运行时守护）

- PID；
- heartbeat stale；
- wall timeout；
- GPU process；
- disk；
- OOM/NaN pattern；
- expected outputs；
- TERM→KILL。

#### PostRunFailureClassifier（实验后分类）

- 读取 stdout/stderr/exit code/artifacts；
- detector chain（按 DetectorProfile 启用）；
- sidecar 缓存；
- failure classification 输出。

#### HealthDiagnosisAgent

只在 PostRunFailureClassifier 无法分类时事件触发，输出建议，不直接 kill。

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

### 12.1 ConvergenceMonitor — Arbor 滑动窗口

参考 Arbor 的 `compute_velocity()` 和 `find_exhausted_parents()`：

```python
class ConvergenceConfig(BaseModel):
    """可配置的滑动窗口参数，默认值来自 Arbor。"""
    window_size: int = 5
    warn_windows: int = 1
    paradigm_shift_windows: int = 2
    stop_windows: int = 3
    noise_units_for_progress: float = 1.0

class ConvergenceMonitor:
    """确定性滑动窗口收敛检测。"""
    def compute(self) -> ConvergenceStatus: ...
```

状态信号（参考 Arbor）：

- `velocity`：最近 window 内 champion IMPROVEMENT 次数
- `parent_exhaustion`：某个 research axis 已无有效子节点
- `warn`：连续 1 个窗口无提升
- `paradigm_shift`：连续 2 个窗口无提升
- `stop`：连续 3 个窗口无提升

阈值可配置，不作为全局硬规则。端到端测试改用：

```text
patch_applied
smoke_passed
metrics_parsed
protocol_intact
```

（替代已删除的旧 VERIFIED/UNVERIFIED 模型。）

### 12.2 StrategySelector + SkillsMiddleware

**FINAL DISPOSITION: StrategyPolicy/StrategyOverlay 已删除。** 替换为：

```text
StrategyDiagnosticAgent
→ 推荐 strategy_skill_ids
→ StrategySelector 做确定性筛选（当前激活 skill、适用范围、生效周期、审批状态、安全约束）
→ DeepAgents SkillsMiddleware 渐进加载 SKILL.md
```

保留的确定性部分：

- 哪些 skill 当前激活；
- 适用范围；
- 生效周期；
- 谁批准；
- 是否影响安全约束。

不再实现 prompt overlay 管理器。

参考：DeepAgents `SkillsMiddleware` + `SKILL.md` 渐进加载。

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

采用 mini-swe-agent 的简约风格——只在每次 query 前检查硬限制：

```python
if call_count >= max_llm_calls:
    raise CognitiveBudgetExceeded
if total_cost >= max_llm_cost:
    raise CognitiveBudgetExceeded
if step_count >= max_agent_steps:
    raise CognitiveBudgetExceeded

# 每次 LLM 调用记录一行到 llm_usage.jsonl
# { cycle_id, role, input_tokens, output_tokens, cost }
```

不需要第一版实现 UsageLedger、cost_class、retry_of、recovery_reserve、ResearchProgressLedger。实际调用和重做都计费——这是 API 的实际花费，不对账目做「有效/无效」分类。

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
├── champions/
│   ├── candidates/
│   │   ├── candidate_001.json
│   │   └── candidate_002.json
│   ├── champion_events.jsonl
│   ├── current_by_contract.json
│   └── transactions/
│       ├── tx_<id>.json
│       └── ...
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
- timeout / heartbeat / RuntimeWatchdog + PostRunFailureClassifier；
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
