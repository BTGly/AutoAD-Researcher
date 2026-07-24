---
name: h2-focus-return-bug
description: ConfigModal close should restore focus to trigger button but returns to input instead
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:31:53.303Z
---

ConfigModal 关闭后焦点没有回到打开弹窗的齿轮按钮（⚙️），而是回到了 API Key 输入框。

**根因：** `useDialogFocus` 的 `returnFocus` 在 useEffect 内捕获 `document.activeElement`，此时焦点已被 `focusTimer` 移到弹窗内第一个输入框，因此 `returnFocus` 指向的是输入框而不是齿轮按钮。

**建议修复：** 在弹窗打开前（`openConfig` 调用时）预先保存齿轮按钮的引用，而不是在 useEffect 里捕获。

**等级：** P1
