---
name: h2-cross-tab-websocket-sync
description: "WebSocket events not broadcast across multiple tabs, requiring manual refresh"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:50:50.219Z
---

WebSocket 连接是标签页级别的。当用户在标签页 A 执行操作（如删除 Source、发送消息），标签页 B 不会收到该事件的推送。

**复现步骤：**
1. 打开两个标签页 A、B 访问同一服务
2. 在 A 中删除一个 Source
3. 观察 B 的内容 — 没有自动更新，需要手动刷新

**根因：** 后端将 WebSocket 事件仅推送到触发该操作的那个连接（`ws_manager.py` 按 `run_id` 广播，但每个标签页有独立的 WebSocket 连接，如果连接不在同一 `run_id` 的订阅列表中就收不到）。

**影响：** 用户可能在多标签页场景中看到不一致的状态，导致重复操作或混淆。

**等级：** P1
