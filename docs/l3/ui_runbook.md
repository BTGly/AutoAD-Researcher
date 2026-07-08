# L3 UI Runbook — Phase 2F

> Phase 2E sealed: 2026-07-04 (c929142)
> Phase 2F update: Research Assistant default view hides engineering details.

## Start the UI

```bash
cd workspace/AutoAD-Researcher
uv run uvicorn autoad_researcher.server.main:app --host 0.0.0.0 --port 8000
cd frontend
bun dev --host 0.0.0.0 --port 5173
```

Open the React app at `http://localhost:5173`.

## Sidebar（所有页面通用）

- **顶部当前任务菜单**：选择已有任务、查看已归档任务、恢复或删除任务
- **新建任务**：创建新的内部 `run_id`，可填写可读任务名
- **当前任务**：显示人类可读任务名；内部 `run_id` 只在 Developer Details 中展示
- **任务摘要**：一句话描述
- 若旧任务没有 profile：显示内部 `run_id`，避免下拉框和当前任务标题不一致
- **重命名任务**：只改显示名称，不改 `run_id` 或 `runs/` 制品目录
- **归档任务**：从默认任务列表隐藏当前任务；不会删除 `runs/` 下的证据链
- **删除已归档任务**：先勾选“显示已归档任务”，选中已归档任务后点击“删除已归档任务”；目录会从 `runs/` 中物理删除
- **Developer Details**：run_id / sources / jobs / artifact paths

## Pages

### 1. 运行配置
API Key 会优先从环境变量 `DEEPSEEK_API_KEY` 或本地 `.env` 自动加载；也可在页面手动输入作为本次会话覆盖。可填写任务名称并创建新任务。系统仍生成内部 `run_id`，高级配置折叠。

页面只显示 API Key 的尾号。若预检或聊天返回 401 且尾号不是当前有效 key，重新输入 key 后点击“保存到本地 .env，下次自动加载”。`.env` 已被 git 忽略，不会提交到仓库。

### 2. 预检执行器
执行 `l3-preflight`。通过后显示下一步 checklist 和终端复现命令。

### 3. 制品浏览器
按 Stage 展示产物文件。每个 Stage 有中文说明和 ⭐ 推荐文件。

### 4. 执行监控
先显示执行摘要（单元完成数、GPU 设备），原始 JSON 折叠。

### 5. 最终审阅
三栏结论：补丁与管线 / 执行完成度 / 科学结论。含人话解释。

### 6. 研究助手（Phase 2F）
默认面向研究者，不再把第 6 页当作 artifact 调试台。

主页面只展示：
- 当前任务名与任务摘要
- 当前状态：正在确认研究目标 / 等待生成实验输入 / 等待审批代码修改 / 等待审批真实执行 / 可查看最终报告
- 数据集是否已配置
- 正常聊天气泡，不显示内部 mode 标签
- 研究目标草案
- 当前流程步骤和下一步动作
- 到达相应阶段后才显示的审批按钮

三种模式：
- **意图澄清**：描述研究想法，系统整理成可审计研究意图草案
- **运行解释**：基于 artifacts 解释当前运行状态
- **下一步建议**：基于实验结果建议下一轮方向

**自动任务命名（Phase 2E 保留）**：
- 首次在研究助手中发送消息后，系统自动调用 LLM 生成任务名和摘要
- 任务名要求：中文 6-14 字或英文 3-8 词，具体表达研究目标
- 保存为 `runs/{run_id}/ui_chat/task_profile.json`
- 不会包含 run_id、API key 或路径
- 生成失败时自动降级为"未命名研究任务"
- 任务名显示在侧边栏和页面 1/2/6 的顶部 banner

**Phase 2F 降噪规则**：
- 默认不显示 raw run_id、绝对路径、Provider、stage 名称或 artifact 文件名
- `ui_chat/intent_draft.json`、`approvals/intent_confirmation.json`、`approval_gate_report.json` 等只在“开发者信息”中出现
- Pipeline 输入准备默认只显示“请先确认研究目标 / 生成实验输入 / 实验输入已准备好”
- HITL gate 默认显示为流程步骤，不展示 `patch_planner`、`patch_applicator`、`runner_execute` 表格
- Patch approval 和 real execution approval 只有相关 pipeline artifact 已生成时才显示
- “查看发送给 LLM 的上下文”移动到默认折叠的“开发者信息”

**开发者信息**：
- 默认折叠
- 保留 run_id、artifact_dir、provider、dataset_root、available_stages
- 保留 raw artifact names、approval gate status 和发送给 LLM 的上下文
- 用于审计和调试，不作为普通研究者默认工作区

## Browse an Existing Run
侧边栏任务下拉框选择已有任务。若任务已归档，先勾选“显示已归档任务”，选择后可点击“恢复任务”。

## Clean Up Test Tasks
测试任务很多时，推荐流程：

1. 选中测试任务。
2. 点击“归档任务”，让它从默认任务列表消失。
3. 勾选“显示已归档任务”，再次选中它。
4. 点击“删除已归档任务”。

删除会物理移除 `runs/{run_id}` 目录。需要保留证据链的任务只归档，不要删除。

## Limitations
- 研究助手不执行 pipeline，只写 approval JSON
- 真实 L3 仍需在终端手动运行，并设置 `AUTOAD_L3_REAL_EXECUTION_ALLOWED=1`
- Web 登录、多用户、数据库和远程审批不在 Phase 2F 范围内
