# 04. WebSocket 实时刷新

## 1. 目标

在不改变现有事件写入路径的前提下，使 `experiment.*` 事件通过现有 WebSocket 推送到前端，驱动实验工作台自动刷新。

## 2. 设计约束

- **不修改实验模块的 `append_event()` 调用** — 不在 25+ 个调用点增加额外 `ws_manager.broadcast()`
- **继续使用现有事件路径**：`append_event()` → `events/events.jsonl` → `ws.py` polling → 前端
- **不做双向 broadcast** — 不存在直接 broadcast 和 WS polling 两次推送的重复问题
- **向后兼容** — 现有 `source.*`、`job.*`、`artifact.*`、`assistant.*`、`toast.*` 事件不受影响

## 3. 当前 WebSocket 数据流

```
实验模块
  → append_event(run_dir, event_type, payload)
    → events/events.jsonl
      ← ws.py: load_events_since(run_dir, last_event_id) (每 0.8 秒)
        → ws.send_json({type: evt["type"], ...payload})
          → 前端 onWsMessage()

连接时同时回放历史事件 (load_events_since from event_id=0)
```

现有 `ws.py` 代码（`server/routes/ws.py:14-58`）：

```python
@router.websocket("/api/runs/{run_id}/ws")
async def websocket_endpoint(ws: WebSocket, run_id: str):
    ...
    last_event_id = 0

    # Replay existing events on connect
    for evt in load_events_since(run_dir, last_event_id):
        if not _is_transient_event(evt):
            await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
        last_event_id = evt["event_id"]

    # Background polling
    async def poll_events():
        nonlocal last_event_id
        while True:
            for evt in load_events_since(run_dir, last_event_id):
                await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
                last_event_id = evt["event_id"]
            await asyncio.sleep(0.8)
```

## 4. 后端改动

**文件：** `src/autoad_researcher/server/routes/ws.py`

### 4.1 WS 消息 envelope 补齐 `event_id` 和 `created_at`

```diff
- await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
+ await ws.send_json({
+     "type": evt["type"],
+     "event_id": evt["event_id"],
+     "created_at": evt["created_at"],
+     **(evt.get("payload", {})),
+ })
```

改动量：两行（replay 和 polling 各一）。

向后兼容说明：现有前端 `onWsMessage` 按 `msg.type` 分发，不认识 `event_id`/`created_at` 的事件会忽略这些新字段，不产生行为变化。

### 4.2 无需其他后端改动

禁止在每个实验模块中增加：

```python
append_event(run_dir, "experiment.xxx", payload)
ws_manager.broadcast(run_id, {"type": "experiment.xxx", ...})
```

同一事件会被 broadcast 一次（直接），又被 WS polling 捞到一次（0.8 秒后），造成前端重复展示。

## 5. 前端改动

### 5.1 `frontend/src/lib/types.ts` — WSMessage 扩展

```diff
 export interface WSMessage {
   type: string;
+  event_id?: number;
+  created_at?: string;
   // ... 现有字段
 }
```

### 5.2 `frontend/src/App.tsx` — onWsMessage 增加 `experiment.*` 分支

```typescript
// 在 onWsMessage 回调中增加 (lines ~482-554)
if (msg.type.startsWith('experiment.')) {
  handleExperimentEvent(msg);
  return;
}
```

```typescript
function handleExperimentEvent(msg: WSMessage) {
  // 通知 ExperimentPage 重新加载投影
  // 使用事件总线或回调
  onExperimentEvent?.(msg);
}
```

### 5.3 `frontend/src/components/ExperimentPage.tsx` — 防抖刷新

```typescript
function ExperimentPage({ runId, sessionId, onExperimentEvent, ... }: Props) {
  const [projection, setProjection] = useState<ExperimentProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const debounceTimer = useRef<number | null>(null);

  // 初始加载
  useEffect(() => {
    loadProjection(runId, sessionId);
  }, [runId, sessionId]);

  // WebSocket 事件 → 防抖 → 重新加载
  useEffect(() => {
    if (!onExperimentEvent) return;
    const handler = (msg: WSMessage) => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
      debounceTimer.current = window.setTimeout(() => {
        loadProjection(runId, sessionId);
      }, 300);  // 300ms 防抖
    };
    // 注册事件监听
    const unsubscribe = onExperimentEvent(handler);
    return () => {
      unsubscribe?.();
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, [runId, sessionId, onExperimentEvent]);

  async function loadProjection(runId: string, sessionId: string | null) {
    setLoading(true);
    try {
      const data = await getExperimentProjection(runId, sessionId);
      setProjection(data);
    } finally {
      setLoading(false);
    }
  }

  // 渲染 ...
}
```

### 5.4 防抖策略说明

| 场景 | 行为 |
|------|------|
| 收到 1 个 `experiment.attempt.finalized` | 300ms 后请求一次投影 |
| 0.2 秒内连续收到 5 个事件 | 合并为 1 次请求（最终仅 1 次载荷） |
| WebSocket 重连 | replay 多个事件 → 合并为 1 次请求 |
| 用户手动刷新 | 直接请求投影，不受防抖限制 |

### 5.5 WebSocket 重连恢复

当前 `useWebSocket` 已有自动重连机制（5 秒 interval 检查 socket 状态）。重连后：

1. 自动回放 `events.jsonl` 中的所有事件（包含 `experiment.*`）
2. 前端收到 replay 事件 → 触发投影刷新
3. 页面恢复最新状态

不依赖浏览器内存维持状态。

## 6. `experiment.*` 事件列表

以下事件现已存在于 `events.jsonl` 中，提交四仅需前端理解它们：

| 事件类型 | emit 位置 | payload 内容 |
|----------|-----------|-------------|
| `experiment.session.created` | `session_store.py:123` | `{session_id, status}` |
| `experiment.idea_tree.created` | `idea_tree.py:179` | `{session_id, tree_revision}` |
| `experiment.idea_tree.mutated` | `idea_tree.py:358` | `{session_id, mutation, tree_revision}` |
| `experiment.cognitive_commit.appended` | `cognition.py:82` | `{session_id, commit_id, tree_revision}` |
| `experiment.observation_snapshot.written` | `cognition.py:103` | `{session_id, cycle_id, tree_revision}` |
| `experiment.attempt.created` | `attempt_service.py:99` | `{session_id, attempt_id, attempt_purpose, idempotency_key}` |
| `experiment.attempt.queued` | `attempt_service.py:124` | `{session_id, attempt_id, pipeline_job_id}` |
| `experiment.attempt.running` | `attempt_execution.py:78` | `{session_id, attempt_id, pid}` |
| `experiment.attempt.finalized` | `attempt_execution.py:188` | `{session_id, attempt_id, runtime_status}` |
| `experiment.attempt.retry_queued` | `attempt_service.py:159` | `{session_id, attempt_id, retry_of, retry_count}` |
| `experiment.attempt.reconnected` | `coordinator_recovery.py:163` | `{session_id, attempt_id}` |
| `experiment.coordinator.checkpoint.recorded` | `coordinator_recovery.py:70` | `{session_id, cycle_id, tree_revision}` |
| `experiment.coordinator.recovered` | `coordinator_recovery.py:138` | `{session_id, action, reason}` |
| `experiment.coordinator.context_pruned` | `coordinator.py:306` | `{session_id, cycle_id, retained_outcome_refs}` |
| `experiment.coordinator.compact_cycle.committed` | `coordinator.py:400` | `{session_id, cycle_id, next_action, tree_mutations}` |
| `experiment.coordinator.exploratory_cycle.committed` | `coordinator.py:491` | `{session_id, cycle_id, next_action, tree_mutations}` |
| `experiment.coordinator.exploratory_cycle.fallback` | `coordinator.py:502` | `{session_id, cycle_id, reason}` |
| `experiment.strategy.filtered` | `strategy.py:179` | `{session_id, available_skills}` |
| `experiment.champion.rolled_back` | `promotion.py:502` | `{session_id, candidate_id, event_id}` |
| `experiment.champion.promoted_and_merged` | `promotion.py:555` | `{session_id, candidate_id, event_id, trunk_commit}` |
| `experiment.stop_policy.evaluated` | `stop_policy.py:128` | `{session_id, decision, reason}` |
| `experiment.convergence.alert` | `convergence.py:208` | `{session_id, level, window_index, max_attempts}` |
| `experiment.cognitive_budget.usage_recorded` | `cognitive_budget.py:75` | `{session_id, call_count, total_cost, step_count}` |

前端不需要理解所有 payload 字段。收到 `experiment.*` 事件后统一走防抖 → 重新请求投影。

## 7. 参考来源

| 来源 | 复用等级 | 说明 |
|------|----------|------|
| 现有 `ws.py` | `[REUSE]` | 沿用 polling 架构，只加 event_id/created_at |
| 现有 `event_service.py` | `[REUSE]` | 沿用 load_events_since 读取 |
| 现有 `App.tsx` onWsMessage | `[REUSE]` | 沿用消息分发模式，加 experiment.* 分支 |

## 8. 测试

### 后端

1. WS 消息 envelope 包含 `event_id` 和 `created_at`
2. 现有 `source.*` / `job.*` / `artifact.*` 消息格式不受影响
3. `_is_transient_event` 过滤正常

### 前端

1. `experiment.*` 事件触发投影刷新
2. 多个事件合并为一次请求
3. WebSocket 重连后恢复最新状态
4. 切换 run/Session 后清理前一个的观测数据
5. 手动刷新页面后恢复最新状态

## 9. 不重复证明

**问题：** 事件同时被 `append_event()` 写入 + `ws_manager.broadcast()` 发送，前端会收到两次。

**验证：** 查看所有事件 emit 点，确认它们只调用 `append_event()`，不额外调用 `ws_manager.broadcast()`。

```bash
# 确认没有模块同时做这两件事（除了 ws.py 本身的 polling 消费）
rg "ws_manager\.broadcast" src/autoad_researcher/experiment/
# → 预期：无匹配
```

## 10. 验收

- 后台运行实验时，实验工作台自动更新（0.8-1.5 秒延迟）
- 切换 run/Session 后只显示对应数据
- WebSocket 重连后不重复显示事件卡片
- 关闭再打开页面后恢复最新状态
- 现有 `source.*` / `job.*` / `assistant.*` / `toast.*` 事件不受影响
