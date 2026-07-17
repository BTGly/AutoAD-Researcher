# 开发计划 01：ExperimentSession 与环境准备

## 1. 目标

把当前已确认任务接入实验控制面，并复用现有 `environments/` 子系统形成可恢复的环境准备流程。

完成后应实现：

```text
confirmed input task
→ ExperimentSession
→ experiment_environment_prepare Job
→ probe
→ EnvironmentPlan
→ policy
→ build
→ validate
→ observed snapshot
→ Session READY
```

---

## 2. 当前基础

可复用：

- `EnvironmentPlan`
- `EnvironmentPermissions`
- UvVenv / PipVenv / Conda / ExistingPython Adapter
- Policy Gate
- `run_environment_build_steps()`
- Validation registry
- `EnvironmentBuildResult`
- bounded revision loop
- shell=False executor

当前缺口：

- 没有主流程调用；
- 没有 Session；
- 没有环境准备 Job；
- 没有 ValidationContextCollector；
- GPU compute 是预填 boolean；
- snapshot 主要由 plan 声明生成；
- 没有真实 host/repository probe。

---

## 3. 实现范围

### PR-001A（前置）：V2 → 实验接线基础 — reconcile-style confirm

> **在 01A 之前必须完成。否则整套环境/实验组件做完后没有触发入口。**

**目标：** 从当前 `TaskBridge.confirm()` 写入 `input_task.yaml` 之后，幂等地创建 ExperimentSession 和环境准备 Job。关键原则：confirm 端点做成 **reconcile-style command**——每次调用都可以安全重放，恢复三种半完成状态。

**当前状态（需改动）：**
- `TaskBridge.confirm_experiment_task()` 硬编码 `execution_mode="plan_only"`
- `ExperimentTaskDraft` 的 `execution_mode` 永远是 `Literal["plan_only"]`
- V2 Orchestrator 的 `task_action_allowed()` 只允许 `plan` 模式
- 没有 `ExperimentStarter` 概念
- 确认后直接 return，不产生任何 Job
- 重复确认会报 `FileExistsError`

**核心问题——当前断点的精确位置：**

```python
# task_bridge.py 当前行为
if input_task.yaml exists:
    raise FileExistsError
```

这会导致：

```text
input_task.yaml 写成功
→ 进程在创建 Session 前崩溃
→ 用户重试确认
→ FileExistsError
→ 无法进入 Starter
```

正确修法不是增加队列层，而是把确认端点改成 **reconcile-style command**：重复调用安全重放，不是报错。

**具体改动（最小化、可增量）：**

| 文件 | 改动 |
|------|------|
| `assistant/v2/task_bridge.py` `ExperimentTaskDraft` | `execution_mode` 扩展为 `Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]` |
| `assistant/v2/task_bridge.py` | 新增 `confirm_or_load_existing(task_id)` —— 首次写文件，重复调用时检查 hash 一致后复用已确认 draft |
| `assistant/v2/orchestrator.py` `handle()` | `task_action_allowed()` 不强制阻塞 agent-assisted 模式 |
| `server/routes/runs.py` confirm 端点 | `TaskBridge.confirm_or_load_existing()` → 若 `execution_mode != "plan_only"` → `ExperimentStarter.on_task_confirmed()` |
| **新增** `assistant/v2/experiment/starter.py` | `ExperimentStarter.on_task_confirmed()`：幂等 create_or_get Session + 幂等 create_or_get environment Job |

**不需要新增的：**

- `experiment_start` Job（过设计——环境安装本身就在 Worker Job 里，HTTP 确认只做几个轻量文件状态操作）
- 另一套状态机
- 新的队列
- Coordinator 参与启动

**启动链（完整）：**

```text
POST confirm
  ↓
TaskBridge.confirm_or_load_existing(task_id)
  ↓ 得到 confirmed ExperimentTaskDraft
  ↓
若 execution_mode == plan_only → 返回 confirmed task（现有行为不变）
  ↓
否则
  ↓
ExperimentStarter.on_task_confirmed(run_dir, confirmed_task)
  ↓
SessionStore.create_or_get(task_hash)          ← 幂等
  ↓
JobStore.create_or_get(                        ← 幂等
    job_type=experiment_environment_prepare,
    idempotency_key=environment_prepare:{session_id}
)
  ↓
返回 confirmed task + session_id + job 摘要
  ↓
（环境 probe/build/validate 由 Worker 异步执行——不是 HTTP 请求做的事）
```

**职责分离：**

| 组件 | 职责 |
|---|---|
| TaskBridge | `confirm_or_load_existing`：冻结用户任务 + 重复调用安全重放 |
| confirm route | 调用 Starter；不直接操作 SessionStore |
| ExperimentStarter | V2 Task → ExperimentSession 映射 + 幂等接线 + 环境 Job 创建 |
| ExperimentSessionStore | Session 持久化 create_or_get 与 revision |
| PipelineJobStore | 环境准备 Job 的 create_or_get |
| Worker | 真正 probe / build / validate——HTTP 请求不参与 |

### 3.1 幂等恢复语义

`confirm_or_load_existing`：

```text
首次调用：
  → 写 input_task.yaml
  → 写 source report
  → 更新 pending draft → confirmed

重复调用：
  → 检查 source report.task_id 匹配
  → 检查 summary_sha256 / task_hash 一致
  → 读取已确认 draft
  → 继续调用 Starter（安全重放）
```

不报 `input_task.yaml already exists`。

`ExperimentStarter.on_task_confirmed` 恢复三种半完成状态：

```text
Session 不存在 → 创建
Session 已存在 → 复用

环境 Job 不存在 → 补建
环境 Job 已存在 → 复用
```

| 半状态 | 恢复动作 |
|---|---|
| 只有 input_task，没有 Session | 创建 Session → 创建 Job |
| 有 Session，没有环境 Job | 补建 Job |
| Session 和 Job 都存在 | 返回 reused |

不需要跨文件事务，不需要额外队列。

### 3.2 传给 Session 的参数

权威输入是已确认的 `input_task.yaml`，不是 `summary.json`：

```python
session_store.create_or_get(
    run_id=run_id,
    task_ref="input_task.yaml",
    task_hash=canonical_input_task_hash,
    execution_mode=confirmed_task.execution_mode,
    repository_ref=resolved_repository_ref_or_none,
    budget=resolved_budget,
)
```

当前 TaskBridge 生成的 `InputTask` 包含 `run_id / request / source_ids / user_idea / constraints`。而 `baseline / dataset / compute_budget` 当前允许为空。

因此 Session 先创建为 `CREATED`，随后由环境准备流程通过 RepositoryProbe 和 Adapter 补齐 repository target、baseline entrypoint、dataset binding 和 evaluation protocol。不应因用户未精确指定 entrypoint 就拒绝创建 Session。

### 3.3 task_hash 计算规则

当前 `task_id` 基于 `summary.json` 的 SHA 生成，适合检测"摘要确认前是否被修改"，但不应直接作为实验 Session 的最终任务身份。

分开保存：

```text
summary_sha256       — 确认草案未过期
task_hash            — ExperimentSession 幂等身份
authorization_hash   — execution_mode + approval/policy revision
```

`task_hash` 根据确认后 `input_task.yaml` 的规范化内容计算：

```python
task_hash = sha256(
    canonical_json(
        InputTask.model_validate_yaml(input_task_yaml).model_dump()
    )
)
```

不要直接 hash YAML 原始文本（字段顺序、空行或序列化格式变化可能产生不同 hash）。

`execution_mode` 不进入 `task_hash`：同一个科研任务从 `approve_each_step` 改为 `agent_assisted_after_approval` 不应识别为不同任务。但执行模式变化不能静默覆盖——应追加授权修订记录。

### 3.4 不完整事实不应阻止 Session 创建

当前 TaskBridge 不保证 baseline、dataset 或 repository_ref 全部已确定。测试也明确接受这些字段为空。因此 Starter 不应要求所有执行事实完整后才创建 Session。

状态流：

```text
Session CREATED
  ↓
实验 readiness 补齐
  ↓
repository target resolved
evaluation contract resolved
baseline entrypoint resolved
environment requirements resolved
  ↓
ENVIRONMENT_PENDING
```

`repository_ref` 缺失不等于 Starter 失败。多个可执行仓库且无法确定目标时进入 `readiness blocked`，不能随便选择 `source_ids[0]`。

### 3.5 OutcomeCard → Coordinator 触发（第二个隐含接头）

当前设计中，AttemptFinalizer 写 OutcomeCard 后 Coordinator 如何触发是隐式的。明确如下：

```text
AttemptFinalizer 原子写 outcome_card.json
  ↓
追加 attempt.finalized Event
  ↓
ResultIntegrationService 消费 Event
  ↓
按 attempt_id + outcome_hash 幂等整合
  ↓
写 ResultIntegrationEvent
  ↓
创建 coordinator_decision Job
  ↓
Coordinator Compact Cycle
```

幂等键：`integrate:{attempt_id}:{outcome_card_hash}`

如果 V1 暂时使用文件轮询，必须保存 `last_integrated_attempt_id` 和 `last_integrated_outcome_hash`，否则 Worker 重启后可能重复建立 decision boundary。

### 3.6 验收标准（001A 完整版）

除了已有检查项（agent-assisted 启动、plan-only 不变、同 task_hash 不重复），还需覆盖：

| # | 验收项 |
|---|---|
| 1 | `confirm_or_load_existing` 首次写文件，重复调用检查 hash 一致后复用 |
| 2 | `input_task.yaml` 写成功、Session 创建前崩溃 → 重试恢复（不报 FileExistsError） |
| 3 | Session 已创建、environment Job 未创建 → `on_task_confirmed` 补齐 Job |
| 4 | Session 和 Job 均存在 → 重复调用返回 reused |
| 5 | 相同 summary_sha256、不同最终 InputTask → 不复用 Session |
| 6 | task_hash 相同但 execution_mode 冲突 → 不静默覆盖，记录授权修订 |
| 7 | 多个 repository source 无明确 target → 进入 readiness blocked |
| 8 | `plan_only` → 永远不调用 Starter |
| 9 | Event 至少包含：`experiment.start_requested`、`experiment.session.created/reused`、`experiment.environment_prepare.queued` |
| 10 | Worker 重启后仍能继续（Job 可重复 claim） |

### 3.1 ExperimentSession schema

新增：

```text
experiment/session.py
experiment/session_store.py
```

字段至少包括：

```text
session_id
run_id
task_ref
task_hash
status
repository_ref
environment_status
environment_snapshot_ref
baseline_status
budget
created_at
updated_at
revision
```

状态：

```text
CREATED
ENVIRONMENT_PENDING
ENVIRONMENT_RUNNING
ENVIRONMENT_FAILED
READY_FOR_BASELINE
BASELINE_RUNNING
READY
FAILED
CANCELLED
```

### 3.2 ExperimentStarter（接口详见 PR-001A）

`ExperimentStarter.on_task_confirmed(run_dir, confirmed_task)` — 幂等 create_or_get Session + 幂等 create_or_get environment Job。由 confirm route 直接调用；不通过中间 Job。支持恢复三种半完成状态。

### 3.3 Job 类型

增加：

```text
experiment_environment_prepare  — 环境准备（idempotency_key: environment_prepare:{session_id}）
```

Worker 重复 claim 时不可重复创建。环境安装和 probe 在后台 Worker Job 中执行。

### 3.4 Probe 层

新增：

```text
environments/probe.py
environments/context_collector.py
```

探测：

- host OS；
- Python；
- uv/pip/conda；
- CUDA runtime；
- `nvidia-smi`；
- torch import；
- torch CUDA tensor compute；
- repository dependency files；
- README/entrypoint candidates；
- existing environment；
- project smoke candidate。

所有命令：

- shell=False；
- 有 timeout；
- 保存 stdout/stderr；
- 对 secret 做脱敏。

### 3.5 EnvironmentPlan provider

优先级：

```text
确定性 repo facts
→ 现有配置
→ 用户明确要求
→ LLM 只补歧义
```

LLM 只能输出结构化 EnvironmentPlan，不能直接执行。

### 3.6 Observed snapshot

新增观察值构建函数：

```text
build_observed_environment_snapshot(
    plan,
    build_result,
    validation_context,
    validation_report,
)
```

snapshot 至少保存：

- 实际 Python/CUDA/torch；
- package inventory hash；
- environment path；
- repository commit；
- GPU capability；
- validation report hash；
- project smoke evidence。

GPU device 编号不写入 snapshot。

---

## 4. 目录与 Artifact

```text
environment/
├── host_probe.json
├── repository_probe.json
├── plan_r0.json
├── policy_r0.json
├── build_r0/
├── validation_context_r0.json
├── validation_report_r0.json
├── plan_r1.json
├── ...
└── snapshot.json
```

---

## 5. 开发步骤

### PR 01A：Session 与幂等 Starter

- Session schema/store；
- task hash；
- create-or-get；
- Job 创建；
- Event；
- 无真实环境执行。

### PR 01B：Probe 与 ContextCollector

- Python/CUDA/GPU probe；
- package/import/file/command facts；
- 实际 `gpu_compute_ok`；
- fixture runner。

### PR 01C：Environment Job 接线

- Worker dispatch；
- plan→policy→build→collect→validate；
- Session 状态更新；
- artifact 落盘。

### PR 01D：Observed Snapshot 与 Revision

- snapshot；
- 失败 context；
- 最多两次 revision；
- revision lineage。

---

## 6. 检验方案

### 6.1 单元测试

1. 同一 task 重复启动只产生一个 Session；
2. 同一 idempotency key 只产生一个 Job；
3. illegal path、sudo、shell metacharacter 被拒绝；
4. GPU probe fixture 正确填 `gpu_available/gpu_compute_ok`；
5. snapshot 使用 observed value，不信任 plan 伪造值；
6. revision parent/revision 连续；
7. max revision 超限后停止。

### 6.2 集成测试

#### 场景 A：Existing Python 成功

```text
fixture repo
→ existing_python
→ import 成功
→ CPU smoke 成功
→ snapshot
→ Session READY_FOR_BASELINE
```

#### 场景 B：uv venv 成功

```text
pyproject
→ uv environment
→ install
→ import
→ project smoke
```

#### 场景 C：GPU 不可用

- required GPU validation 失败；
- Session `ENVIRONMENT_FAILED`；
- 明确 failure code；
- 不启动 baseline。

#### 场景 D：第一次依赖计划错误，第二次修订成功

- revision 0 import fail；
- revision context；
- replacement plan；
- revision 1 pass。

### 6.3 故障注入

- build timeout；
- worker 中断后重启；
- logs 写入中断；
- snapshot 前崩溃；
- duplicate job；
- repo dirty；
- plan 声明 GPU 正常但真实 compute fail。

### 6.4 验收标准

- Environment 子系统在非测试代码中存在真实调用；
- Session 可以从中断状态恢复；
- GPU compute 是真实 probe；
- snapshot 可复现并包含 hash；
- 环境失败绝不进入 baseline；
- 重复请求不重复安装；
- 所有步骤有 artifact 和 Event。
