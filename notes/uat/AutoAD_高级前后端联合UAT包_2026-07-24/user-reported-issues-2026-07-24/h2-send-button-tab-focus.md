---
name: h2-send-button-tab-focus
description: Send button cannot be reached via Tab even when enabled
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:35:17.742Z
---

ChatInput 中的“发送”按钮无法通过 Tab 键聚焦。

**现象：** 输入文字后按钮从 `disabled` 变为 `enabled`，但 Tab 仍然跳过它（textarea → PlusMenu → 状态栏）。用户必须用鼠标点击或按 `Enter`（当焦点在 textarea 时）来发送。

**根因（推测）：** `AppButton` 没有显式的 `tabIndex`，当按钮从 `disabled` 切换为 `enabled` 时，React 的 re-render 没有让浏览器重新识别它为可聚焦元素。也可能与 `chat-composer-row` 的 CSS 布局和 AppButton 的 `pointer-events` / `visibility` 过渡有关。

**建议修复：** 给发送按钮加 `tabIndex={value.trim() ? 0 : -1}`，确保文字存在时按钮进入 Tab 序列。

**等级：** P2
