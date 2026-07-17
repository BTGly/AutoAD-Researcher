# 开发计划 04：Experiment Job、GPU 资源与训练监控

## 1. 目标

把真实训练从阻塞式一次调用升级为可恢复、可取消、可监控的持久 Experiment Job，并新增 GPU ResourceLease 和确定性 Sentinel。

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

### 2.8 建议 Job 类型（同原有）

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

## 5. Sentinel

> **直接复用 SWE-Together 的 `eval_infra_sentinel.py` 设计**（`/root/autodl-tmp/repos/SWE-Together/src/eval_infra_sentinel.py`）。

### 5.1 核心设计原则

- **Gating predicate（来自 SWE-Together line 491）：** 如果训练产生了有意义的输出（patch > 200 字节 / metrics.json 非空），即使 stderr 中有错误，也算 `ok` 而非 `run_failed`。语义："重新跑这个实验不太可能产生不同结果"→不需要重跑。
- **排除式计分（来自 SWE-Together line 261-273）：** run_failed 的 attempt **不计为 0.0，而是从评分中排除**。这防止基础设施不稳定拖低系统指标。
- **Sidecar pattern（来自 SWE-Together）：** `trial_infra.json` 写在 attempt 目录里，`classify_or_load()` 优先读缓存，`--skip-existing` 时零开销复用。

### 5.2 Detector Chain（按特异性排序，first match wins）

全部 detector 定义直接复用 SWE-Together 的 `DETECTORS` 列表，按 AutoAD 训练场景调整：

| # | Detector | 触发条件 | verdict |
|---|----------|----------|---------|
| 1 | `empty_transcript` | stdout+stderr 为 0 字节 | `run_failed` — Worker 启动了但进程没产生任何输出 |
| 2 | `oom_error` | stderr 匹配 `CUDA out of memory` / `torch.cuda.OutOfMemoryError` | `run_failed` — 可重试，建议缩减 batch size |
| 3 | `cuda_error` | stderr 匹配 `CUDA error` / `CUBLAS_STATUS_*` / `cuDNN error` | `run_failed` — GPU 运行时故障 |
| 4 | `disk_full` | stderr 匹配 `No space left on device` | `run_failed` |
| 5 | `provider_402_balance` | stderr 匹配 `Insufficient Balance`（API 调用场景） | `run_failed` |
| 6 | `provider_429_quota` | stderr 匹配 rate limit + quota/exhaustion | `run_failed` |
| 7 | `provider_401_auth` | stderr 匹配 `Invalid API key` / auth failure | `run_failed` |
| 8 | `python_import_error` | stderr 匹配 `ModuleNotFoundError`（非目标仓库） | `run_failed` — 环境问题 |
| 9 | `no_agent_progress` | step ≥ 5，0 次 successful edit，空 patch | `run_failed` — 代码修改未生效（借用 SWE-Together 的 MIN_TURNS_FOR_NO_PROGRESS = 5） |

### 5.3 再分类（来自 SWE-Together 的 rerun policy）

| Rerun-worthy（可重试） | Fair-zero（算 agent 的成绩） |
|------------------------|----------------------------|
| oom_error, cuda_error, disk_full | wall_timeout（超时→TIMED_OUT 是硬 cap） |
| provider_429_quota, provider_401_auth | provider_402_balance（余额不足→不算系统问题） |
| empty_transcript | NaN/Inf→FAIL_FAST（算训练失败，不计为 infra） |
| python_import_error（环境问题） | exit 0 但指标全部为 0（可能作弊） |

### 5.6 BatchSupervisor — 批处理中的紧急事件通道

Sentinel 产生紧急事件时**不直接唤醒 Coordinator**。通道是：

```text
Sentinel
  → 持久 AttemptHealthEvent (写入 EventStore)
  → BatchSupervisor
  → BatchFailurePolicy
  → 必要时建立 Coordinator decision boundary
```

不掉头向 Coordinator 推送消息（不通过 WebSocket 或活跃 Agent 的通知）。事件在 EventStore 中持久化，Coordinator 下次到达 decision boundary 时读取。

### 5.7 批次内 variant 的失败耦合关系

每个批量实验需要声明 `coupling`：

| 类型 | 一个 variant OOM 后 |
|------|-------------------|
| `independent` | 当前 Attempt 失败，其他 sibling 继续 |
| `shared_assumption` | 暂停尚未启动的 sibling，已运行的继续 |
| `gang` | 取消整个批次并释放全部资源 |

### 5.8 确定性 Sentinel 规则

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
known error patterns（见 5.2 detector chain）
expected outputs（metrics.json / checkpoint）
```

确定性动作：

```text
NaN/Inf                          → FAIL_FAST (非 infra)
OOM                              → FAILURE(OOM) (run_failed, 可重试)
heartbeat stale + PID dead       → LOST/FAILED
heartbeat stale + PID alive      → SUSPECTED_STALL → grace(30s) → SIGTERM → 等(30s) → SIGKILL
wall timeout                     → TIMED_OUT
exit 0 + outputs complete        → COMPLETED
exit 0 + outputs missing         → INVALID_COMPLETION
exit 0 + metrics.json 存在       → gating predicate: 算 ok (不是 infra)
run_failed                     → 排除不计分，可 retry（最多 3 次）
```

### 5.5 排除式计分

```python
def effective_score(attempt_dir: Path, raw_score: float | None) -> float | None:
    verdict = classify_attempt(attempt_dir)  # 读 trial_infra.json 或重算
    if verdict.status == "run_failed":
        return None  # 排除了——不算 0.0
    return raw_score or 0.0
```

训练结果统计中，run_failed 的 attempt 被排除，不参与 KEEP/DISCARD 和 convergence 计算。

---

## 6. HealthDiagnosisAgent

只在以下事件触发：

- Sentinel 无法分类；
- heartbeat 正常但 GPU 长期低利用；
- stderr 新型模式；
- 重复 retry；
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

### PR 04D：Sentinel

- heartbeat；
- error patterns；
- TERM/KILL；
- output completion。

### PR 04E：HealthDiagnosisAgent

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

### 9.3 Sentinel 故障注入

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

> OutcomeCard 只有一个写入口，不是 Worker/Sentinel/Coordinator 三方争写。

### 运行前

RetryPolicy 检查历史 Attempt：
- 是否已成功
- 是否 protocol violated
- 是否达到 retry 上限
- 是否值得重试

不写 OutcomeCard。

### 运行中

Sentinel 发现异常（OOM / NaN / heartbeat stale / timeout / process lost）追加：

```text
attempts/<id>/health_events.jsonl
```

每行一条：

```json
{"event": "OOM", "timestamp": "...", "stderr_snippet": "CUDA out of memory"}
```

然后要求 Worker 终止或标记进程。

Sentinel 不写 OutcomeCard。

### 进程结束后

**唯一的`AttemptFinalizer`** 写 `outcome_card.json`。它综合：

- execution result
- health events
- metrics
- protected hash 对比
- EvaluationContract 校验
- failure code

### Coordinator 读取后

Coordinator 不修改 OutcomeCard，只写：

```text
decision.json          — KEEP / DISCARD / CONFIRM
cognitive_commit.jsonl — 科研决策记录
IdeaTree mutation      — 更新节点状态/insight
```
