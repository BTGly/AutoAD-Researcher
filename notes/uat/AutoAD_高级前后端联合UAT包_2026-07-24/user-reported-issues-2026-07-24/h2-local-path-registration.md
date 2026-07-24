---
name: h2-local-path-registration
description: AI claims to register local paths but UI shows no new sources
metadata:
  node_type: memory
  type: feedback
  originSessionId: 1ea96e6c-d0b0-4254-a9c9-6f9a368f7b29
  modified: 2026-07-24T13:45:21.308Z
---

用户提供本地路径（PatchCore 仓库 `/root/autodl-tmp/repos/patchcore-inspection` 和 MVTec 数据集 `/root/autodl-tmp/.autodl/autoad/AI4S/projects/AutoAD-Researcher/data/mvtec_ad`）后，AI 在对话中回复“已登记”，但：

1. **右侧 Sources 面板没有新增条目** — 仍然只显示旧的两个失败记录（GitHub URL clone 超时 + MVTec 网页）
2. **后端 API 没有创建新的 source 记录** — 所有 run 的 sources 列表均为空
3. **AI“已登记”只是话术** — LLM 在对话中声称做了什么，但实际没有调用任何后端 API

**影响等级：P0（严重）**

实际用户场景中，大部分科研用户：
- 在服务器/GPU 环境工作
- 代码仓库和数据集通常已经在本地的特定路径
- 需要 AI 能读取本地路径、索引已有材料，而不是依赖 GitHub clone

当前系统只能通过 **URL** 注册资料（GitHub 链接、网页链接），没有“登记本地路径”的机制。

**建议：**
- 增加一个本地路径登记能力，让 Sources 可以注册 `local_repo` / `local_dataset` 类型
- Intent Clarifier / Source Acquisition 应该能读取本地文件系统路径，而不是只能 clone 远程仓库
- AI 的输出“已登记”必须与实际后端操作绑定，不能只是 LLM 生成的回复文本
