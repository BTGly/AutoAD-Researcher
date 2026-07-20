# 04. WebSocket 实时刷新

## 1. 目标

在不修改现有事件写入路径和 WebSocket envelope 的前提下，使 `experiment.*` 事件触发实验工作台重新读取只读投影。

## 2. 当前 WebSocket 事实

当前路径是：

```text
实验模块
  → append_event(run_dir, event_type, payload)
    → events/events.jsonl
      → ws.py load_events_since(run_dir, last_event_id)
        → ws.send_json({"type": evt["type"], ...payload})
          → 前端 onWsMessage()
```

连接时会回放已有事件，后台按现有间隔轮询新事件。前端 `useWebSocket` 已有重连检查。

本提交不改变这些事实，也不把事件 payload 当作科研状态。

## 3. 设计约束

- 不修改实验模块的 `append_event()` 调用。
- 不在实验模块增加 `ws_manager.broadcast()`。
- 不修改 `ws.py` 的消息 envelope。
- 不新增 `event_id`、`created_at` 到 WebSocket 消息。
- 不新增前端事件总线。
- 不根据事件 payload 拼接 Idea、Attempt、Outcome 或 Champion 状态。
- 所有科研状态刷新都重新请求投影 API。
- 现有 `source.*`、`job.*`、`artifact.*`、`assistant.*`、`toast.*` 行为不改变。

审阅报告提出扩展 WS envelope 的建议，但当前页面只需要“投影可能失效”信号；事件 ID 和时间已经由后端投影的 ActivityCard 提供，放入 WS envelope 会扩大协议修改面而没有首版收益。

## 4. 前端改动

### 4.1 `frontend/src/App.tsx`

在现有 `onWsMessage` 中增加最小分支：

```typescript
if (msg.type.startsWith('experiment.')) {
  setExperimentRefreshTick(value => value + 1);
  return;
}
```

`experimentRefreshTick` 只表示“需要重新读取”，不携带实验数据。

将它传入 `ExperimentPage`。不要把 `msg` 保存为前端实验状态，也不要在这里理解各个实验事件的 payload 字段。

### 4.2 `frontend/src/components/ExperimentPage.tsx`

页面负责：

1. mount 或 `runId` 变化时请求一次投影；
2. `sessionId` 变化时请求对应投影；
3. `experimentRefreshTick` 变化时启动 300ms 防抖；
4. 防抖结束后请求 `GET /api/runs/{run_id}/experiment/projection`；
5. 请求完成后以返回快照替换页面投影。

建议逻辑：

```typescript
useEffect(() => {
  void loadProjection(runId, sessionId);
}, [runId, sessionId]);

useEffect(() => {
  if (experimentRefreshTick === 0) return;
  const timer = window.setTimeout(() => {
    void loadProjection(runId, sessionId);
  }, 300);
  return () => window.clearTimeout(timer);
}, [experimentRefreshTick, runId, sessionId]);
```

实际实现必须遵循当前 React 类型和 API helper 的写法，不复制不存在的 `onExperimentEvent` 事件总线接口。

### 4.3 刷新和请求竞态

- `runId` 变化时清除旧的防抖计时器；
- `sessionId` 变化时不能把旧 Session 的投影显示到新 Session；
- 可以使用请求序号或 AbortController 防止旧请求覆盖新请求，但不能新增第二套实验状态；
- 请求失败时保留上一份有效投影并显示错误提示；
- 初次请求失败不能把“请求失败”伪装成“无 Session”。

## 5. WebSocket 重连

重连后当前 WebSocket 会回放事件。多个 replay 事件只会造成刷新计数连续变化，页面通过防抖合并为一次投影请求。

页面恢复依赖持久化投影，而不是依赖浏览器内存里的事件卡片：

```text
重连
  → 现有 WS replay experiment.*
    → refresh tick
      → 防抖
        → GET projection
          → 展示当前权威状态
```

不在重连时追加重复 Activity 卡片；Activity 始终由后端投影重新装配。

## 6. 事件类型清单的处理方式

本文件不再维护带 payload 字段的静态事件表。当前分支中多个事件字段已经与旧计划不同，而且部分事件没有 `session_id`，需要通过准确引用关联 Store。

编码前如确实需要事件来源表，必须：

1. 对当前分支所有 `append_event()` 调用执行精确检索；
2. 从源码和测试读取事件类型及 payload；
3. 只记录已核对的字段；
4. 明确该表仅用于开发者参考，前端刷新逻辑不得依赖它。

首版前端只依赖 `type` 前缀：

```text
experiment.* → 刷新投影
其他事件     → 保持现有 App.tsx 行为
```

## 7. 测试

### 后端

本提交不应新增 `ws.py` envelope 测试，因为 envelope 不变。继续运行现有 WS 和事件测试，确认：

- replay 仍可用；
- polling 仍可用；
- transient event 过滤不变；
- 原有 source/job/assistant/toast 消息不变。

### 前端与首版验收

当前 `frontend/package.json` 只有 `build`、`lint`、`dev`、`preview`，没有 Vitest、Jest 或 Testing Library。首版不为了这一个页面立即引入新的前端测试框架，也不把组件自动化测试写成现有执行条件。

自动验证使用当前工具链：

1. Python 投影和路由测试由 `pytest` 覆盖；
2. `cd frontend && npm run build`；
3. `cd frontend && npm run lint`；
4. `bash scripts/verify.sh`。

真人 UAT 检查以下行为：

1. `experiment.*` 消息只触发刷新计数，不读取 payload 拼接 UI；
2. 短时间多个事件和重连 replay 最终只请求一次投影；
3. 切换 run/session 后旧请求不能覆盖新投影；
4. 手动刷新直接请求投影；
5. 请求失败保留上一份快照并展示错误；
6. Activity 不重复插入，且超过 100 条时显示截断提示。

未来若前端交互复杂度确实需要组件级自动化测试，再单独引入 Vitest/Testing Library，并在引入后补充对应脚本和 CI 入口。

## 8. 不重复证明

不要修改实验模块以同时调用 `append_event()` 和 `ws_manager.broadcast()`。
检查范围应是当前分支实际的实验目录，并以源码结果为准；不能把“预期无匹配”写成未经验证的事实。

## 9. 验收

- 后台实验状态变化后，工作台通过投影重新加载更新。
- WS envelope 没有新增字段。
- 前端不从事件 payload 生成科研状态。
- 重连后不会重复显示事件卡片。
- 切换 run/session 后不显示旧数据。
- Activity 首版最多显示最近 100 条可靠动态，截断状态可见。
- 现有 Chat、Sources、Jobs、Evidence、Toast 行为不回归。
