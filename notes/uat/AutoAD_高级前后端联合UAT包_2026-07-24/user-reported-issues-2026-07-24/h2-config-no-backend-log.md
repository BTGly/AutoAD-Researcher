---
name: h2-config-no-backend-log
description: "ConfigModal save only writes to localStorage, no backend audit trail"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:31:58.647Z
---

配置修改仅保存在浏览器 `localStorage`（key: `autoad_config`），后端没有任何 API 调用或日志记录。

**影响：** 如果用户修改了 API Key 或模型选择，系统无法追踪谁在何时改了配置。对于需要审计合规的场景（如科研实验配置溯源），这是一个缺口。

**等级：** Observation（P2，取决于是否需要审计能力）
