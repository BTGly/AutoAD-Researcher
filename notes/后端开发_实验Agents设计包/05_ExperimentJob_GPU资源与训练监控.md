# 开发计划 04：Experiment Job、GPU 资源与训练监控

## 1. 目标

把真实训练从阻塞式一次调用升级为可恢复、可取消、可监控的持久 Experiment Job，并新增 GPU ResourceLease、RuntimeWatchdog 和 PostRunFailureClassifier。

LLM 不实时盯训练。

---

## 2. Job 设计（双层状态）

### 2.1 PipelineJob 层（4 状态，与现有系统共用）

现有 `PipelineJob.status` **保持不变**，实验 Job 不破坏全局兼容性：

```text
queued / running / completed / failed
```

PipelineJob 只负责队列生命周期。

### 2.2 ExperimentAttempt 层（7 状态 + 运行时扩展）

新增独立 `runtime_status`，只用于实验 Attempt 的进程生命周期：

```text
QUEUED
STARTING
RUNNING
TERMINATING
COMPLETED
FAILED
TIMED_OUT
CANCELLED
LOST
```

### 2.3 Attempt 运行时字段（新增）

```text
pid
process_group_id
heartbeat_at
cancel_requested_at
job_timeout_sec
retry_of
retry_count
max_retries
failure_code
retry_exhausted
```

`runtime_status = FAILED` + `retry_exhausted = true` 即表示死信，不需要额外 `dead` 状态。

### 2.4 Heartbeat

Worker 每 10 秒原子更新 `attempts/<id>/heartbeat.json`：

```json
{
  "pid": 12345,
  "status": "running",
  "step": 320,
  "epoch": 4,
  "loss": 0.173,
  "last_metric": 0.912,
  "timestamp": "..."
}
```

无法提取 step/loss 时允许 `null`，但 PID 和 timestamp 必须存在。

### 2.5 Cancel 协议

```text
cancel_requested_at 被设置
→ Worker 发 SIGTERM 给 process group
→ 等 30 秒
→ 仍存活则 SIGKILL
→ runtime_status = CANCELLED
```

### 2.6 Retry Lineage

```text
attempt_002.retry_of = attempt_001
```

旧 Attempt 不重新打开。退避：

```python
delay_seconds = min(60, 5 * (2 ** retry_count))
```

### 2.7 三层超时（9 天内实现，不做四层沙箱）

```text
job_timeout_sec       — 整个 Attempt 硬上限
command_timeout_sec   — 单个命令超时
max_agent_steps       — Agent 循环步数上限 / wall_time_limit
```

### 2.9 Attempt Purpose（流程语义，非死板分类器）

attempt_purpose 不由 Agent 推断，由创建 Attempt 的调用路径确定：

| 调用路径 | purpose | counts_toward_convergence |
|----------|---------|--------------------------|
| BaselineRunner | `baseline` | ❌ |
| Coordinator DISPATCH | `exploration` | ✅ |
| 补 seed / B_test | `confirmation` | ❌ |
| NoiseEstimator 多 seed | `noise_calibration` | ❌ |
| Executor bounded fix | `repair` | ❌ |

convergence 只统计 `attempt_purpose == exploration && SCIENTIFICALLY_EVALUABLE`。

```python
attempt_purpose: Literal["baseline", "exploration", "confirmation", "noise_calibration", "repair"]
```

```text
experiment_baseline
experiment_attempt
experiment_confirmatory
```

---

## 3. ResourceLease

### 3.1 Schema

```text
lease_id
attempt_id
worker_id
device_ids
required_device_count
required_vram_mb
allocated_at
expires_at
heartbeat_at
status
```

### 3.2 GpuAllocator

第一版本地 allocator：

- 读取 `nvidia-smi`；
- 检查设备 total/used/free；
- 检查 AutoAD active leases；
- 选择满足显存要求的 GPU；
- 原子创建 lease；
- 设置 `CUDA_VISIBLE_DEVICES`；
- heartbeat 续租；
- 任务结束释放；
- lease 过期回收。

不依赖 LLM。

---

## 4. 长训练 Runner

当前阻塞式 `subprocess.run()` 可继续用于短命令；长训练改为：

```text
subprocess.Popen
```

需要：

- shell=False；
- process group；
- stdout/stderr 增量写；
- PID；
- start time；
- timeout；
- TERM grace；
- KILL；
- exit code；
- restart recovery。

### 4.1 Launch Profile — 第一版只支持单 GPU 单进程

```text
第一版正式边界：
  single-node, single-GPU, single training process (python train.py)
```

检测到以下情况直接返回 `UNSUPPORTED_LAUNCH_PROFILE`：

```text
torchrun
torch.distributed.launch
WORLD_SIZE > 1
nproc_per_node > 1
```

PyTorch 官方文档确认 torchrun 会在节点上启动多个训练进程并由本地 elastic agent 管理 worker group；worker failure 时整个 group 可能被停止和重启。仅监控 `Popen.pid` 不够——`start_new_session + os.killpg()` 可以信号化整个进程组，但不能涵盖所有脱离或重建的子进程。

后续支持单机多 GPU 时应新增：

```text
LaunchProfile.TORCHRUN_LOCAL + CgroupV2ExecutionScope
# 组合: process group + cgroup descendant accounting + nvidia-smi GPU PID reconciliation
```

`pstree` 仅作诊断工具，不能作为资源所有权真源。多节点或无法使用 cgroup 时交给外部 scheduler。

### 4.2 Attempt heartbeat

训练适配器定期输出：

```text
status
epoch
step
loss
best_metric
last_improvement_step
checkpoint
updated_at
```

无法修改训练脚本时，Worker 至少写 process heartbeat，并从日志提取有限 progress。

---

## 5. 运行时监控与实验后分类（两个独立组件）

> **关键修正：** 运行时进程守护（RuntimeWatchdog）与实验后失败分类（PostRunFailureClassifier）是两个职责不同的组件，拆分设计，不混在一个"Sentinel"中。

---

### 5.1 RuntimeWatchdog

运行在 Attempt 期间的守护协程，只关心进程生死和基础健康状况。

轮询间隔 15–30 秒，检查：

```text
PID / process group
heartbeat age（> 2×INTERVAL → stale）
stdout growth（> 5min 无增长 → stalled）
GPU PID（nvidia-smi 验证进程仍在该 GPU）
GPU utilization / memory
disk
wall time
checkpoint mtime（> 周期阈值 → 可能 hang）
expected outputs（metrics.json / checkpoint）
```

确定性动作：

```text
NaN/Inf                          → FAIL_FAST (health_event)
OOM                              → OOM_DETECTED (health_event)
heartbeat stale + PID dead       → WORKER_LOST
heartbeat stale + PID alive      → SUSPECTED_STALL → grace(30s) → SIGTERM → 等(30s) → SIGKILL
wall timeout                     → TIMED_OUT
exit 0 + outputs complete        → COMPLETED (无事件)
exit 0 + outputs missing         → OUTPUTS_MISSING (health_event)
```

输出：

```text
attempts/<id>/health_events.jsonl
```

每行一条：

```json
{"event": "OOM_DETECTED", "timestamp": "...", "stderr_snippet": "CUDA out of memory"}
```

RuntimeWatchdog 不写 OutcomeCard，不做失败分类。进程结束后由 AttemptFinalizer 统一综合。

---

### 5.2 PostRunFailureClassifier

实验进程已经结束后运行。参考 SWE-Together 的 `eval_infra_sentinel.py`（`[COPY]`，Apache-2.0，vendor 并保留 LICENSE/NOTICE），

**仅复用：**
- detector-chain / first-match-wins 模式；
- `classify_or_load()` sidecar 缓存；
- rerun-worthy / non-rerun policy；
- 排除式计分语义。

**不复用：**
- provider auth/quota/balance detector（coding-agent 特有）；
- `empty_transcript` / `no_agent_progress` / `patch_bytes_threshold`；
- Claude Code / OpenCode transcript 解析器（AutoAD 使用不同的 trace 格式）。

#### 5.2.1 DetectorProfile

AutoAD 不原样启用 SWE-Together 全部 DETECTORS。PostRunFailureClassifier 通过 `DetectorProfile` 选择场景适用的检测器。

```python
class DetectorProfile(str, Enum):
    GPU_TRAINING = "gpu_training"
    CODING_AGENT = "coding_agent"
    CUSTOM = "custom"

class FailureClassifierConfig(BaseModel):
    profile: DetectorProfile = DetectorProfile.GPU_TRAINING
    enabled_detectors: list[str] | None = None
    disabled_detectors: list[str] = []
```

优先级：

```text
enabled_detectors 显式提供 → 只启用指定 detector
否则 → 加载 profile 默认列表 → 再移除 disabled_detectors
```

#### 5.2.2 GPU_TRAINING 默认启用列表

| Detector | FailureCode | 默认策略 |
|----------|------------|---------|
| `oom_error` | `OOM` | 允许缩 batch 时重试一次 |
| `cuda_runtime_error` | `CUDA_RUNTIME_ERROR` | 瞬时错误可重试 |
| `cudnn_error` | `CUDNN_ERROR` | 瞬时错误可重试 |
| `disk_full` | `DISK_FULL` | 不立即重试，先释放空间 |
| `process_spawn_failed` | `PROCESS_SPAWN_FAILED` | 自动重试 1 次 |
| `worker_lost` | `WORKER_LOST` | 原配置自动重试 |
| `stale_heartbeat` | `STALLED` 或 `WORKER_LOST` | 区分 PID 状态后终止重试 |
| `wall_timeout` | `TIMEOUT_WITH_PROGRESS` / `TIMEOUT_NO_PROGRESS` | 根据是否有 progress 决策 |
| `nan_or_inf` | `NAN_OR_INF` | 不作为纯 infra 重试，交 Executor 或 Coordinator |
| `python_import_error` | `IMPORT_OR_SYNTAX_ERROR` | 交 Executor 有界修复 |
| `metrics_missing` | `METRICS_MISSING` | 检查输出和 parser |
| `invalid_metrics_schema` | `INVALID_METRICS_SCHEMA` | 检查 parser 配置 |

#### 5.2.3 GPU_TRAINING 默认禁用列表

以下 coding-agent 特定 detector 默认禁用（由其他组件处理）：

```text
provider_401_auth           → AgentRuntime / ModelCallPolicy
provider_402_balance        → AgentRuntime / ModelCallPolicy
provider_429_quota           → AgentRuntime / ModelCallPolicy
empty_transcript             → RuntimeWatchdog（进程无输出不直接判定 LOST；LOST 必须由 PID 不存在、process group 不存在或 worker lease 丢失确定）
no_agent_progress            → ExecutorProgressGuard
patch_bytes_threshold        → PatchGate
agent_turn_limit             → CognitiveBudget
tool_call_format_error       → DeepAgents structured-output / tool error handling
```

#### 5.2.4 Detector 顺序与证据优先级

失败分类严格按以下优先级（从可验证结构到 fallback）：

```text
1. 结构化 runtime event（exit_code, signal, metrics schema）
2. process exit code / signal
3. framework adapter 捕获的异常类型
4. output/metrics schema 验证
5. stderr regex fallback（仅作为最后手段，不作为主要真源）
6. UNKNOWN → HealthDiagnosisAgent
```

Regex detector 保留但只能作为 fallback，不能成为主要真源。

#### 5.2.5 Detector 顺序（first match wins）

```text
1. PROTECTED_ARTIFACT_CHANGED
2. OOM / CUDA_RUNTIME_ERROR / CUDNN_ERROR / DISK_FULL
3. NAN_OR_INF
4. IMPORT_OR_SYNTAX_ERROR
5. METRICS_MISSING / INVALID_METRICS_SCHEMA
6. UNKNOWN_RUN_FAILURE
```

越具体的 detector 越靠前。

#### 5.2.5 Sidecar 输出

```json
{
  "classifier_version": "autoad-gpu-v1",
  "profile": "gpu_training",
  "enabled_detectors": ["oom_error", "cuda_runtime_error", "nan_or_inf"],
  "matched_detector": "oom_error",
  "failure_code": "OOM",
  "attempt_category": "run_failed",
  "retryable": true
}
```

写入 `attempts/<id>/failure_classification.json`。

---

### 5.3 AttemptFinalizer

唯一致力 OutcomeCard 的组件，进程结束后统一综合以下来源：

```text
execution_result.json     — Worker 写入
health_events.jsonl        — RuntimeWatchdog 写入
failure_classification.json — PostRunFailureClassifier 写入
metrics.json               — 训练输出
protected_hashes.json      — EvaluationContract SHA 校验
```

输出 `outcome_card.json` 和 `attempt_category`，供 Coordinator 读取。AttemptFinalizer 不允许分段写入——要么整体成功，要么整体失败。

#### 三阶段 Artifact 写入时序

| 阶段 | 组件 | 写入 |
|------|------|------|
| 运行前 | RetryPolicy | 不写 OutcomeCard |
| 运行中 | RuntimeWatchdog | `health_events.jsonl` |
| 进程结束 | AttemptFinalizer | `outcome_card.json`（唯一写入口） |
| Coordinator 读取后 | Coordinator | `decision.json`、`cognitive_commit.jsonl` |

Coordinator 不修改 OutcomeCard，只读。

---

### 5.4 BatchSupervisor — 批处理中的紧急事件通道

RuntimeWatchdog 或 PostRunFailureClassifier 产生事件时**不直接唤醒 Coordinator**。通道是：

```text
RuntimeWatchdog / PostRunFailureClassifier
  → 持久 AttemptHealthEvent (写入 EventStore)
  → BatchSupervisor
  → BatchFailurePolicy
  → 必要时建立 Coordinator decision boundary
```

不掉头向 Coordinator 推送消息（不通过 WebSocket 或活跃 Agent 的通知）。事件在 EventStore 中持久化，Coordinator 下次到达 decision boundary 时读取。

### 5.5 批次内 variant 的失败耦合关系

每个批量实验需要声明 `coupling`：

| 类型 | 一个 variant OOM 后 |
|------|-------------------|
| `independent` | 当前 Attempt 失败，其他 sibling 继续 |
| `shared_assumption` | 暂停尚未启动的 sibling，已运行的继续 |
| `gang` | 取消整个批次并释放全部资源 |

### 5.6 排除式计分

```python
def effective_score(attempt_dir: Path, raw_score: float | None) -> float | None:
    verdict = classify_attempt(attempt_dir)  # 读 failure_classification.json 或重算
    if verdict.attempt_category == "run_failed":
        return None  # 排除了——不算 0.0
    return raw_score or 0.0
```

训练结果统计中，run_failed 的 attempt 被排除，不参与 KEEP/DISCARD 和 convergence 计算。

---

## 6. HealthDiagnosisAgent

只在以下事件触发：

- PostRunFailureClassifier 无法分类（failure_code = UNKNOWN）；
- 但 heartbeat 正常且 GPU 长期低利用；
- stderr 出现新型模式；
- 重复 retry 仍失败；
- loss 异常但未达到确定性 stop；
- process、GPU、文件状态冲突。

输出：

```text
HEALTHY
LIKELY_SLOW
LIKELY_STUCK
LIKELY_CONFIG_ERROR
LIKELY_NUMERICAL_FAILURE
INSUFFICIENT_EVIDENCE
```

Agent 不能直接 kill。动作由 HealthPolicy 决定。

---

## 7. 训练完成后的采集

Worker 生成：

```text
execution_result.json
metrics.json
resource_usage.json
output_manifest.json
checkpoint_manifest.json
```

资源至少包含：

- wall；
- peak VRAM；
- mean GPU util；
- CPU/RAM；
- exit；
- timeout；
- lease；
- energy 可选。

---

## 8. 开发步骤

### PR 04A：Experiment Job 与 Worker dispatch

- schema；
- store；
- claim；
- retry；
- event；
- fixture command。

### PR 04B：GpuAllocator / ResourceLease

- local probe；
- atomic lease；
- expiry；
- recovery；
- CUDA_VISIBLE_DEVICES。

### PR 04C：Popen Runner

- process group；
- incremental logs；
- cancel；
- timeout；
- restart observation。

### PR 04D：RuntimeWatchdog

- heartbeat 轮询；
- PID/process group 验证；
- TERM/KILL；
- stdout growth & checkpoint mtime；
- health_events.jsonl 写入。

### PR 04E：PostRunFailureClassifier

- FailureClassifierConfig / DetectorProfile；
- detector chain（GPU_TRAINING profile）；
- sidecar 缓存；
- failure_classification.json 写入。

### PR 04F：AttemptFinalizer

- 综合 execution_result + health_events + metrics + SHA 校验；
- outcome_card.json 输出；
- 三阶段写入时序保证。

### PR 04G：HealthDiagnosisAgent

- event trigger；
- compact evidence；
- advisory output；
- policy。

---

## 9. 检验方案

### 9.1 Job 测试

- duplicate idempotency；
- two workers claim；
- worker crash；
- retry；
- cancellation；
- stale running recovery。

### 9.2 ResourceLease

- 单 GPU 互斥；
- 多 GPU 请求；
- 显存不足等待；
- expired lease 回收；
- process 结束释放；
- worker crash 后回收；
- 环境 probe 使用短 lease。

### 9.3 RuntimeWatchdog 故障注入

fixture 脚本：

1. 正常训练；
2. NaN；
3. OOM 模拟；
4. hang；
5. heartbeat 停止但进程活；
6. exit 0 无 outputs；
7. 忽略 SIGTERM；
8. disk full 模拟；
9. checkpoint 长期不更新。

### 9.4 LLM 调用边界

验证：

- 正常训练 0 次 LLM health call；
- 已知 OOM 0 次；
- 明确 timeout 0 次；
- 只有 unknown/conflict 才调用；
- Agent 建议不会直接执行 kill。

### 9.5 验收标准

- 长训练不占用 Web/API 调用栈；
- Worker 重启后能判断进程/Job 状态；
- GPU 不超卖；
- 正常训练无需 LLM；
- timeout、NaN、OOM、hang 有确定性结果；
- 所有日志增量可用；
- 资源和输出有 manifest。

---

## 10. FailurePolicy 表

> failure_code → AttemptCategory → 自动动作。这是第一版必须固定的策略，否则重试行为会在不同 Worker 中各自实现。

| failure_code | AttemptCategory | 自动动作 |
|-------------|-----------------|---------|
| `WORKER_LOST` | RUN_FAILED | 原配置自动重试，最多 2 次 |
| `TEMPORARY_GPU_UNAVAILABLE` | RUN_FAILED | 指数退避后自动重试 |
| `TRANSIENT_IO_ERROR` | RUN_FAILED | 自动重试 1 次 |
| `PROCESS_SPAWN_FAILED` | RUN_FAILED | 自动重试 1 次；再次失败归档 |
| `OOM` | RUN_FAILED | InterventionContract 允许减 batch 时自动修复重试 1 次，否则交 Coordinator |
| `TIMEOUT_WITH_PROGRESS` | RUN_FAILED | 预算允许时提高 timeout 重试 1 次 |
| `TIMEOUT_NO_PROGRESS` | RUN_FAILED | 不自动重试，交 Coordinator |
| `NAN_OR_INF` | RUN_FAILED | 不作为纯 infra 重试；交 Executor repair 或 Coordinator |
| `METRICS_MISSING` | RUN_FAILED | 不重复原命令；交 Executor 检查输出逻辑 |
| `IMPORT_OR_SYNTAX_ERROR` | RUN_FAILED | 有界 repair，不消耗完整实验重试 |
| `USER_CANCELLED` | RUN_FAILED | 不重试 |
| `PROTECTED_ARTIFACT_CHANGED` | PROTOCOL_VIOLATED | 排除，不重试 |
| `EVALUATION_CONTRACT_CHANGED` | PROTOCOL_VIOLATED | 排除，不重试 |
| `PATCH_OUT_OF_SCOPE` | PROTOCOL_VIOLATED | 排除，不重试 |
| 正常结束且指标可解析 | SCIENTIFICALLY_EVALUABLE | 进入 DecisionEngine |

**关键原则：** 自动重试 ≠ 重新让 Coordinator 做科研决策。只对明确的瞬时运行失败自动重试。
OOM、NaN、timeout 这类可能与方案有关的错误，不能一律当作基础设施故障清除。

---

## 11. 三阶段 Artifact 写入（不同组件写不同 Artifact）

> OutcomeCard 只有一个写入口，不是 Worker/RuntimeWatchdog/Coordinator 三方争写。详见 §5.3 AttemptFinalizer 时序。

### 运行前

RetryPolicy 检查历史 Attempt：
- 是否已成功
- 是否 protocol violated
- 是否达到 retry 上限
- 是否值得重试

不写 OutcomeCard。

### 运行中

RuntimeWatchdog 发现异常追加 `health_events.jsonl`（见 §5.1）。

RuntimeWatchdog 不写 OutcomeCard。

### 进程结束后

AttemptFinalizer 写 `outcome_card.json`（见 §5.3）。它综合：

- execution result
- health events
- failure classification
- metrics
- protected hash 对比
- EvaluationContract 校验

### Coordinator 读取后

Coordinator 不修改 OutcomeCard，只写：

```text
decision.json          — KEEP / DISCARD / CONFIRM
cognitive_commit.jsonl — 科研决策记录
IdeaTree mutation      — 更新节点状态/insight
```

---

## 12. ResultIntegration → Coordinator decision Job

Worker 中的确定性函数 `integrate_outcome()` 完成触发，不需要常驻 ResultIntegrationService。

### Decision group 聚合

```text
单 Attempt: decision_group_id = attempt_id
便宜 batch: decision_group_id = batch_id
```

Batch 创建时冻结成员集合：

```json
{
  "decision_group_id": "batch_001",
  "expected_attempt_ids": ["attempt_101", "attempt_102", "attempt_103"]
}
```

`integrate_outcome()` 只做：

```python
completed_ids >= expected_attempt_ids
```

terminal 包括 `completed / failed / cancelled / invalid`——否则一个失败 Attempt 会导致 batch 永远等不到"全部成功"。

同一 decision group 内所有 Attempt 完成前，只记录结果不调 Coordinator。完成后创建一次 decision Job：

```text
coordinator_decision:{decision_group_id}:{integration_revision}
```

payload：

```python
CoordinatorDecisionJobPayload:
    session_id
    decision_group_id
    trigger_kind            # single / batch
    outcome_refs            # OutcomeCard 引用列表
    tree_revision
    idempotency_key
```
