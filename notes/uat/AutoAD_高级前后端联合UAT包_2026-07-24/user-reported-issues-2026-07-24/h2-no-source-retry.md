---
name: h2-no-source-retry
description: No retry mechanism for failed source acquisitions
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:51:08.046Z
---

当 Source 采集失败（如 GitHub clone 超时、网页抓取失败）时，UI 没有提供重试按钮。用户只能：
1. 删除该 Source（点 ×）
2. 重新发送消息让 AI 重新登记

没有“重试采集”的按钮或 API。

**等级：** P2
