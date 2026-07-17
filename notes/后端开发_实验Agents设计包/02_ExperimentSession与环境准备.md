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
| `assistant/v2/task_bridge.py` | `ExperimentTaskDraft.execution_mode` 扩展为 `Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]` |
| `assistant/v2/task_bridge.py` | 新增 `confirm_or_load_existing()` — **先写 confirmed draft（权威确认记录），再物化 YAML/report** |
| `server/routes/runs.py` confirm 端点 | 新增 `ConfirmExperimentTaskRequest(execution_mode)` — 用户明确选择执行模式 |
| `server/routes/runs.py` confirm 端点 | 返回 `ExperimentTaskConfirmationResult` — 含 session_id + environment_job_id + disposition |
| **新增** `assistant/v2/experiment/starter.py` | `ExperimentStarter`：幂等 create_or_get Session + 幂等 create_or_get environment Job |
| `assistant/v2/job_service.py` | 新增 `idempotency_key` 字段 + `create_or_get_pipeline_job()` — **锁内完成 load → 查 key → 分配 job_id → 写入** |

**核心修正——写入顺序（vs 当前真实代码）：**

当前代码先写 `input_task.yaml`，后写 `pending_experiment_task → confirmed`。如果中间崩溃，`input_task.yaml` 存在但 `pending_task` 仍是 pending——重试被 `FileExistsError` 锁死。

**改为：** 先写包含完整 `InputTask` payload 的 confirmed draft（权威确认记录），再物化 `input_task.yaml`。confirmed draft 是持久化的 write-ahead record——YAML 和 source report 可从它重建。

```text
1. 验证 pending draft + 当前 summary SHA256
2. 生成 confirmed draft（execution_mode + confirmed_at + summary_sha256 + task_id + 完整 InputTask）
3. 原子写 confirmed draft                     ← 持久化提交点
4. 从 confirmed draft 派生 input_task.yaml
5. 从 confirmed draft 派生 source report
```

恢复时兼容所有合法半状态：

```text
confirmed draft 存在、YAML 缺失 → 重建 YAML
confirmed draft 存在、report 缺失 → 重建 report
三个都存在                  → 一致性校验后复用
内容冲突                    → 409，不覆盖
兼容旧顺序：draft=pending 但 YAML 存在 → 读取 YAML，视为等同于 draft 内的 InputTask → 补写 confirmed draft
```

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
    idempotency_key=environment_prepare:{session_id}:r0
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

### 3.1 幂等恢复语义（修正后的 reconcile 协议）

`confirm_or_load_existing`：

```text
首次调用：
  → 生成 confirmed draft（execution_mode + confirmed_at + 完整 InputTask）
  → 原子写 confirmed draft                    ← 权威提交点
  → 从 confirmed draft 派生 input_task.yaml
  → 从 confirmed draft 派生 source report

重复调用（全状态兼容）：
  → confirmed draft 存在、YAML 缺失 → 从 draft 重建 YAML
  → confirmed draft 存在、report 缺失 → 从 draft 重建 report
  → 三个都存在 → 校阅 consistency 后复用
  → draft=pending 但 YAML 存在(旧顺序遗留) → 读 YAML 补写 confirmed draft
  → 内容冲突 → 409, don't overwrite
```

`ExperimentStarter.on_task_confirmed` 三种 disposition：

| 半状态 | 恢复动作 | disposition |
|--------|----------|-------------|
| Session + Job 都没有 | 创建 Session → 创建 Job | `created` |
| 有 Session，没有 Job | 补建 Job | `repaired` |
| Session 和 Job 都存在 | 返回复用 | `reused` |
| execution_mode == plan_only | 不调用 Starter | `plan_only` |

### 3.2 Confirm API 请求与响应

```python
class ConfirmExperimentTaskRequest(BaseModel):
    execution_mode: Literal[
        "plan_only",
        "approve_each_step",
        "agent_assisted_after_approval",
    ]

class ExperimentTaskConfirmationResult(BaseModel):
    task: ExperimentTaskDraft
    session_id: str | None = None
    session_status: str | None = None
    environment_job_id: str | None = None
    disposition: Literal["plan_only", "created", "repaired", "reused"]
```

**execution_mode 由用户在 confirm 请求中明确选择，不由 LLM 或 Orchestrator 自行提升。** 对话在 Plan Mode 中生成 task draft；执行授权发生在确认端点。

### 3.3 PipelineJob create_or_get（修复并发竞争）

当前 `_generate_job_id()` 在文件锁外执行——存在竞争条件。改为在锁内完成全部：

```python
def create_or_get_pipeline_job(run_dir, *, idempotency_key, job_type, payload):
    with _jobs_lock(run_dir):
        jobs = _load_jobs_unlocked(run_dir)
        existing = _find_by_key(jobs, idempotency_key)
        if existing:
            if (existing["job_type"] != job_type
                    or existing["payload"] != payload):
                raise Conflict("same idempotency key, different job identity")
            return existing, False

        job_id = _next_id_from_loaded(jobs)
        job = { "job_id": job_id, ..., "idempotency_key": idempotency_key }
        jobs.append(job)
        _write_jobs_unlocked(run_dir, jobs)
        return job, True
```

五个动作在同一把锁内：load → 查 key → 检查冲突 → 分配 job_id → 写入。

**环境 Job 的 idempotency_key 包含 revision：** `environment_prepare:{session_id}:r{n}` —— 同一 revision 重放复用，新 revision 可建新 Job。

### 3.4 EventService 并发 ID 修复

当前 `_next_event_id()` 与 PipelineJob 同类竞争——在锁外读文件求 max ID + 1。使用同款锁修复（或改用 UUID4，但现有 `load_events_since(last_id: int)` 依赖单调整数）。

### 3.5 传给 Session 的参数

confirmed draft 中 `input_task` 是权威数据源；`input_task.yaml` 是派生物化文件：

```text
confirmed draft.input_task
= 科研任务的权威数据与 task_hash 来源

input_task.yaml
= 下游 Pipeline 的兼容性物化文件

task_ref
= 指向该物化文件，便于现有流程读取
```

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

### 3.6 task_hash 计算规则

当前 `task_id` 基于 `summary.json` 的 SHA 生成，适合检测"摘要确认前是否被修改"，但不应直接作为实验 Session 的最终任务身份。

分开保存：

```text
summary_sha256       — 确认草案未过期
task_hash            — ExperimentSession 幂等身份
```

`task_hash` 直接从 confirmed draft 计算（仓库已有 `canonical_sha256()` 处理 key 排序、紧凑 JSON、UTF-8、exclude_none）：

```python
from autoad_researcher.benchmarks.hashing import canonical_sha256

task_hash = canonical_sha256(confirmed_task.input_task)
```

不重新实现第二套 canonical JSON。confirmed draft 是 authority-record——YAML 只负责 Pipeline 兼容输入，不参与身份定义。

`execution_mode` 不进入 `task_hash`：同一个科研任务从 `approve_each_step` 改为 `agent_assisted_after_approval` 不应识别为不同任务。但执行模式变化不能静默覆盖——应追加授权修订记录。

### 3.7 Readiness 状态（独立字段）

Session 主状态不承载 readiness 的子状态。新增独立字段：

```python
readiness_status: Literal["unresolved", "resolving", "ready", "blocked"]
```

与其他 Session 主状态解耦：

```text
Session CREATED + readiness=unresolved
  → 每个缺失字段给出具体 blocking reason
  → 阻断升级为 ENVIRONMENT_PENDING
```

不新增 Session 主状态 `READINESS_PENDING` / `READINESS_BLOCKED`。

### 3.8 Authorization 事件（复用 events.jsonl）

不新建 `authorization_events.jsonl`。仓库已有 `runs/{run_id}/events/events.jsonl` 和统一 `append_event()`。权限变更时在此写：

```text
experiment.authorization.confirmed
experiment.authorization.changed
experiment.authorization.revoked
```

权威位置（三层，各有职责）：

```text
confirmed draft
= 初次用户确认，不可变历史记录

ExperimentSession.authorization
= Session 创建后的当前有效权限（Starter 创建时从 confirmed draft 复制初始值）

events.jsonl
= 权限变化历史（之后的 changed/revoked 追加，不回写 confirmed draft）
```

Starter 首次创建 Session 时复制初始授权；之后的 changed/revoked 只更新 Session 当前值并追加 Event，不回写历史 confirmed draft。

### 3.9 不完整事实不应阻止 Session 创建

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

### 3.10 OutcomeCard → Coordinator 触发

详见计划 04 §12。由 Worker 中的确定性函数 `integrate_outcome()` 完成，按 decision_group_id 聚合：

```text
单 Attempt: decision_group_id = attempt_id
便宜 batch: decision_group_id = batch_id
```

同一 decision group 内所有 Attempt 完成前，只记录结果不调 Coordinator。完成后创建一次 `coordinator_decision` Job。

幂等键：`integrate:{attempt_id}:{outcome_card_hash}`

如果 V1 暂时使用文件轮询，必须保存 `last_integrated_attempt_id` 和 `last_integrated_outcome_hash`，否则 Worker 重启后可能重复建立 decision boundary。

### 3.11 验收标准（001A 完整版）

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

### 3.12 ExperimentSession schema

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
readiness_status
readiness_blockers
environment_revision
authorization
authorization_revision
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

### 3.13 ExperimentStarter（接口详见 PR-001A）

`ExperimentStarter.on_task_confirmed(run_dir, confirmed_task)` — 幂等 create_or_get Session + 幂等 create_or_get environment Job。由 confirm route 直接调用；不通过中间 Job。支持恢复三种半完成状态。

### 3.14 Job 类型

增加：

```text
experiment_environment_prepare  — 环境准备（idempotency_key: environment_prepare:{session_id}:r{n}）
```

Worker 重复 claim 时不可重复创建。环境安装和 probe 在后台 Worker Job 中执行。

### 3.15 Probe 层

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

### 3.16 EnvironmentPlan provider

优先级：

```text
确定性 repo facts
→ 现有配置
→ 用户明确要求
→ LLM 只补歧义
```

LLM 只能输出结构化 EnvironmentPlan，不能直接执行。

### 3.17 Observed snapshot

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
