# Reference Provenance & 独立实现声明

> 本文档记录 AutoAD-Researcher Step 3.6–3.7 在设计阶段参考的公开项目、借鉴的通用工程模式，以及明确排除的复用部分。

---

## 调研项目

| 项目 | URL | 调研时 commit / 版本 | 许可证 | 语言 |
|---|---|---|---|---|
| MiMoCode (OpenCode fork) | 小米内部 fork | `packages/opencode/src/` | MIT (OpenCode) | TypeScript |
| aider | https://github.com/Aider-AI/aider | `5dc9490` (Jun 2025) | Apache 2.0 | Python |
| SWE-agent | https://github.com/SWE-agent/SWE-agent | 调研时 latest | MIT | Python |
| OpenHands SDK | https://github.com/All-Hands-AI/OpenHands | 调研时 latest | MIT | Python |
| mini-swe-agent | https://github.com/ethan0405/mini-swe-agent | 调研时 latest | MIT | Python |
| anomalyco/opencode | https://github.com/anomalyco/opencode | MiMoCode 基于此 fork | MIT | TypeScript |

调研文件位于：

```
references/coding-agents/README.md
```

---

## 借鉴的通用工程模式

以下模式已在公开文献或多个开源项目中广泛使用，不属于任何单一项目的专有表达。

| 通用模式 | 公开示例 | 本项目采用方式 | 未复用 |
|---|---|---|---|
| **规划与执行分离** (plan/build split) | MiMoCode build/plan/compose agent, aider architect/editor coder | 3.6 只读 Patch Planner + 3.7 审批后 Controlled Applicator，中间额外增加人工审批门禁和 preflight 验证 | 未复用参考项目的 agent 调度、LLM prompt、子进程管理代码 |
| **分层权限** (layered allow/deny/ask) | MiMoCode PermissionEngine, 通用 RBAC 模式 | `can_write_path()` 五层交集：deny > ask-approved > allowed > planned ∩ approved > default-deny。每个变更增加 change_id 级别的 audit | 未复用参考项目的 wildcard 匹配、SQLite 规则持久化、Deferred Promise 机制 |
| **显式变更模型** (discriminated change types) | MiMoCode hunk (add/update/delete), aider patch format | `change_kind` enum (create/modify/delete/rename/configuration_only/test_only) + `target_mode` 解耦 | 抽象层不同：本项目是文件级计划，参考项目是 hunk 级补丁格式 |
| **人工审批门禁** | MiMoCode plan_exit tool, aider confirm_ask() | `ApprovalDecision` + `Preflight`（多 SHA 绑定、workspace 作用域、payload 绑定） | 未复用参考项目的 CLI prompt、Y/N/A 交互、Deferred 阻塞模式 |
| **撤销与回滚** (undo/rollback) | SWE-agent undo_edit file history stack | `before_blob` base64 逆序恢复 + `rollback_failed` 指纹校验 | 未复用参考项目的 JSON 状态文件、全局历史栈 |
| **确定性变更** (exact/declarative edits) | SWE-agent exact-match `str_replace`, aider SEARCH/REPLACE block | `before_sha256 + full_after_content/unified_diff`，通过前态哈希和 Payload 校验保证修改对象未漂移 | 未复用 SWE-agent 的 str_replace 文件编辑器、aider 的 fuzzy matching 多级回退 |

---

## 明确未复用的部分

根据当前仓库代码、提交历史和开发记录：

1. **源文件**：未复制、翻译或修改任何参考项目的源文件（`.py`、`.ts`、`.js`、`.rs` 等）。
2. **依赖引入**：未将参考项目源码作为项目依赖或 vendored code 引入。`pyproject.toml` 中无参考项目的包引用。
3. **代码翻译**：未将 TypeScript/Rust 实现逐段翻译为 Python。例如 MiMoCode 的 `patch/index.ts`（4-pass 上下文匹配、hunk 解析、LSP 诊断集成）均未出现在本项目代码中。
4. **交互机制**：未复用 aide 的 `confirm_ask()`、MiMoCode 的 `ctx.ask()` Deferred 模式或 SWE-agent 的命令行阻塞交互。本项目的审批是离线 JSON artifact 合同，通过 `ApprovalRequest` / `ApprovalDecision` / `Preflight` 的 SHA 绑定链路实现。
5. **运行时能力**：未复用参考项目的 LSP 诊断、Tree-sitter AST 编辑、diff 模糊匹配、Git 自动提交或 shell sandbox。本项目的 Applicator 是确定性函数（`_apply_single_change` + `os.replace`），不包含上述运行时逻辑。

---

## 概念启发对照

以下对照仅表示设计思路层面的概念对应，**不表示算法、接口或实现的直接对应**。

```
MiMoCode "allow/deny/ask" 权限分层
  → 启发：多级权限判定顺序
  本实现：五层路径交集（deny > ask > allowed > planned ∩ approved > default-deny）
  差异：未使用 MiMoCode 的 wildcard 匹配、SQLite 规则表、Deferred ask 机制

MiMoCode "plan exit tool" 切换门禁
  → 启发：规划→执行之间需要显式门禁
  本实现：ApprovalDecision SHA 绑定 + Preflight 七组校验
  差异：未使用 MiMoCode 的 plan_exit 工具消息或 agent 模式切换

aider "architect/editor" 角色拆分
  → 启发：设计方案与代码修改分属不同阶段
  本实现：3.6 Planner（只读）+ 3.7 Applicator（审批后写入）
  差异：未使用 aider 的 Coder 子类、edit_format 矩阵或 LLM 驱动的修改循环

SWE-agent "str_replace" 精确匹配
  → 启发：避免模糊匹配导致错误修改
  本实现：before_sha256 哈希校验 + full_after_content 或 unified_diff Payload
  差异：这是不同的确定性策略（哈希 vs 字符串计数），未复用 SWE-agent 的 str_replace_editor

SWE-agent "undo_edit" 撤销
  → 启发：写入操作需要可撤销
  本实现：before_blob base64 逆序恢复 + rollback_failed 指纹校验
  差异：数据结构和恢复机制完全不同（blob 恢复 vs JSON 状态栈）
```

---

## 验证方式

可以通过以下方式独立验证：

```bash
# 1. 确认依赖中无参考项目
grep -E "aider|swe.agent|openhands|mimo" pyproject.toml

# 2. 确认无 vendored 源码
find src -name "*.py" | xargs grep -l "str_replace_editor\|editblock_coder\|plan_exit" 2>/dev/null

# 3. 提交历史审查
git log --all --oneline -- src/autoad_researcher/code_agent/
```

首次实现 commit：`d22a34b`（PatchApplicator）、`1d11a9d`（PatchPlanner）、`162c015`（Payload wiring）。

---

> 根据当前仓库、提交历史和开发记录，本项目未直接复制、翻译或修改上述参考项目的源文件或代码片段，未将其源码作为项目依赖或 vendored code 引入。参考项目仅用于理解公开的工程模式，最终数据模型、权限规则、状态机和实现代码均结合本项目需求独立设计与编写。若本文档中列出某一参考项目与本项目模块的对照关系，该关系仅表示概念启发或设计对照，不表示算法、接口或实现的一一对应。
