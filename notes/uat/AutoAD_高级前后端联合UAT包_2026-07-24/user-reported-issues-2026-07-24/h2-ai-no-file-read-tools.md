---
name: h2-ai-no-file-read-tools
description: "Research dialogue agent has no file reading tools, cannot access local paths"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:51:03.636Z
---

AutoAD 的 Research Dialogue Agent（`research_dialogue_agent.py`）是**纯对话 LLM**，没有任何工具调用能力：

- ❌ 无文件读取工具（`read_file`、`list_directory`）
- ❌ 无代码执行工具
- ❌ 无本地路径搜索工具
- ❌ 无 Git 仓库分析工具

当 AI 说“我会读取本地仓库的目录结构、README 和入口脚本”时，它只是在生成文本承诺 —— 实际上系统从未赋予它这些能力。用户提供的本地路径（如 `/root/autodl-tmp/repos/patchcore-inspection`）只作为文本被 LLM 看到，没有任何后端代码去实际读取文件。

**影响：** 整个“论文理解 → 仓库分析 → 实验方案生成 → 代码修改”闭环在“读取本地仓库”这一步就断了。用户即使有完整的本地代码和数据集，系统也只能基于网页搜索结果（GitHub 页面）来工作。

**等级：** P0
