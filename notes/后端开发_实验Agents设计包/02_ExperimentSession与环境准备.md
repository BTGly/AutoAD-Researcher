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

### PR-001A（前置）：V2 → 实验接线基础

> **在 01A 之前必须完成。否则整套环境/实验组件做完后没有触发入口。**

**目标：** 从当前 TaskBridge.confirm() 写入 input_task.yaml 之后，解锁实验启动。

**当前状态（需改动）：**
- `TaskBridge.confirm_experiment_task()` 硬编码 `execution_mode="plan_only"`
- `ExperimentTaskDraft` 的 `execution_mode` 永远是 `Literal["plan_only"]`
- V2 Orchestrator 的 `task_action_allowed()` 只允许 `plan` 模式
- 没有 `ExperimentStarter` 概念

**具体改动（最小化、可增量）：**

| 文件 | 改动 |
|------|------|
| `assistant/v2/task_bridge.py` `ExperimentTaskDraft` | `execution_mode` 扩展为 `Literal["plan_only", "approve_each_step", "agent_assisted_after_approval"]` |
| `assistant/v2/task_bridge.py` `build_experiment_task()` | execution_mode 默认为 `"plan_only"`，由调用方或 Settings 覆盖 |
| `assistant/v2/orchestrator.py` `handle()` | `task_action_allowed()` 不强制阻塞 agent-assisted 模式 |
| `server/routes/runs.py` confirm 端点 | `TaskBridge.confirm_experiment_task()` 之后，若 `execution_mode != "plan_only"` → 调用 `ExperimentStarter.on_task_confirmed()` |
| **新增** `assistant/v2/experiment/starter.py` | `ExperimentStarter`：创建 `ExperimentSession` + 创建 `experiment_environment_prepare` PipelineJob |

**验收（001A）：**
- `execution_mode=agent_assisted` → confirm 后触发 ExperimentSession 创建
- `execution_mode=plan_only` → 现有行为不变（只写 input_task.yaml）
- 幂等：同一 task_hash 不重复创建 Session

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

### 3.2 ExperimentStarter

职责：

- 根据 task hash 创建或读取 Session；
- 保证幂等；
- 不在调用栈中安装环境；
- 创建 `experiment_environment_prepare` Job；
- 写 Session/Event。

### 3.3 Job 类型

增加：

```text
experiment_environment_prepare
```

payload：

```text
session_id
task_hash
repository_ref
idempotency_key
```

Worker 重复 claim 时不能重复创建环境。

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
