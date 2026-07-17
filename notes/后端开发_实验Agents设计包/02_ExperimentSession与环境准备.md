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

### PR-001A（前置）：V2 → 实验接线基础 — 持久化启动协议

> **在 01A 之前必须完成。否则整套环境/实验组件做完后没有触发入口。**

**目标：** 从当前 TaskBridge.confirm() 写入 input_task.yaml 之后，通过持久化的 `experiment_start` Job 解锁实验启动。关键原则：**confirm HTTP 请求不直接创建 Session**，而是写入持久化启动命令；Worker 异步消费启动命令后创建 Session。

**当前状态（需改动）：**
- `TaskBridge.confirm_experiment_task()` 硬编码 `execution_mode="plan_only"`
- `ExperimentTaskDraft` 的 `execution_mode` 永远是 `Literal["plan_only"]`
- V2 Orchestrator 的 `task_action_allowed()` 只允许 `plan` 模式
- 没有 `ExperimentStarter` 概念
- 确认后直接 return，不产生任何 Job

**为什么用 Job 而非 confirm route 直接创建 Session：**

confirm HTTP 请求如果在创建 Session 的中途崩溃，会留下半完成状态。例如：

```text
input_task.yaml 已写成功
→ 进程崩溃
→ Session 尚未创建
```

用户再次确认会因为 `input_task.yaml already exists` 被挡住。成熟 workflow 系统的核心思想是启动命令必须持久化、具有稳定身份，并在失败后继续或重放。AutoAD 复用现有 `PipelineJobStore` 即可实现。

**具体改动（最小化、可增量）：**

| 文件 | 改动 |
|------|------|
| `assistant/v2/task_bridge.py` `ExperimentTaskDraft` | `execution_mode` 扩展为 `Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]` |
| `assistant/v2/task_bridge.py` `build_experiment_task()` | execution_mode 默认为 `"plan_only"`，由调用方或 Settings 覆盖 |
| `assistant/v2/orchestrator.py` `handle()` | `task_action_allowed()` 不强制阻塞 agent-assisted 模式 |
| `server/routes/runs.py` confirm 端点 | `TaskBridge.confirm_experiment_task()` 之后，若 `execution_mode != "plan_only"` → 追加 `experiment_start` PipelineJob → HTTP 请求结束 |
| **新增** `assistant/v2/experiment/starter.py` | `ExperimentStarter`：由 Worker 消费 `experiment_start` Job 后调用；创建/复用 Session + 创建/复用 `experiment_environment_prepare` Job |
| `assistant/v2/experiment/start_command.py` | `ExperimentStartCommand` 持久化启动命令 schema |

**启动链（完整）：**

```text
用户确认任务
  ↓
TaskBridge.confirm_experiment_task()
  ↓
写 input_task.yaml + source report
  ↓
追加 experiment_start PipelineJob
  ↓
HTTP 请求结束（不创建 Session）
  ↓
Worker claim experiment_start
  ↓
ExperimentStarter.start()
  ↓
ExperimentSessionStore.create_or_get()
  ↓
创建或确认 experiment_environment_prepare Job
  ↓
Session → ENVIRONMENT_PENDING
```

**职责分离：**

| 组件 | 职责 |
|---|---|
| TaskBridge | 确认并冻结用户任务（input_task.yaml） |
| PipelineJobStore | 持久化启动请求 |
| ExperimentStarter | 将确认任务转换为实验 Session |
| ExperimentSessionStore | Session 的创建、读取和 revision |
| Environment Job | 后续真实环境准备 |

### 3.1 持久化启动命令

```python
class ExperimentStartCommand(BaseModel):
    schema_version: Literal[1] = 1

    run_id: str
    task_id: str
    task_ref: str                 # input_task.yaml 路径
    task_hash: str
    confirmation_ref: str         # task_bridge/experiment_task_source_report.json

    execution_mode: Literal[
        "approve_each_step",
        "agent_assisted_after_approval",
    ]

    requested_at: str
    idempotency_key: str          # experiment_start:{run_id}:{task_hash}
```

不要把 baseline、dataset、repository path 等字段再复制进 command。Starter 应读取权威 `input_task.yaml` 和相关 artifact，避免两份状态漂移。

### 3.2 Starter 接口

```python
class ExperimentStarter:
    def start(
        self,
        run_dir: Path,
        command: ExperimentStartCommand,
    ) -> ExperimentStartResult:
        ...
```

返回：

```python
class ExperimentStartResult(BaseModel):
    session_id: str
    session_created: bool
    environment_job_id: str | None
    environment_job_created: bool
    session_status: str
```

### 3.3 Session create-or-get

```python
session = session_store.create_or_get(
    run_id=command.run_id,
    task_ref=command.task_ref,
    task_hash=command.task_hash,
    execution_mode=command.execution_mode,
)
```

随后：

```python
job_store.create_or_get(
    job_type="experiment_environment_prepare",
    idempotency_key=f"environment_prepare:{session.session_id}:{command.task_hash}",
    payload={
        "session_id": session.session_id,
        "task_hash": command.task_hash,
        "task_ref": command.task_ref,
    },
)
```

### 3.4 task_hash 计算规则

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

### 3.5 不完整事实不应阻止 Session 创建

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

### 3.6 OutcomeCard → Coordinator 触发（第二个隐含接头）

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

### 3.7 验收标准（001A 完整版）

除了已有检查项（agent-assisted 启动、plan-only 不变、同 task_hash 不重复），还需覆盖：

| # | 验收项 |
|---|---|
| 1 | `input_task.yaml` 写成功、start Job 创建前崩溃 → 重试可恢复 |
| 2 | start Job 重复 claim → 只产生一个 Session |
| 3 | Session 已创建、environment Job 未创建 → 重试补齐 Job |
| 4 | Session 和 Job 均存在 → 重复启动返回 reused |
| 5 | 相同 summary_sha256、不同最终 InputTask → 不复用 Session |
| 6 | task_hash 相同但 execution_mode 冲突 → 不静默覆盖，记录授权修订 |
| 7 | 多个 repository source 无明确 target → 进入 readiness blocked |
| 8 | `plan_only` → 永远不创建 start Job |
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

### 3.2 ExperimentStarter（接口详见 PR-001A §3.3）

由 Worker 消费 `experiment_start` Job 后调用。核心接口：`start(run_dir, ExperimentStartCommand) → ExperimentStartResult`。通过 `session_store.create_or_get()` 和 `job_store.create_or_get()` 保证幂等。

### 3.3 Job 类型

增加：

```text
experiment_start                — 持久化启动命令（见 PR-001A §3.1）
experiment_environment_prepare  — 环境准备（payload 见 PR-001A §3.3）
```

Worker 重复 claim 时不可重复创建。`experiment_start` 的 idempotency_key 为 `experiment_start:{run_id}:{task_hash}`。

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
