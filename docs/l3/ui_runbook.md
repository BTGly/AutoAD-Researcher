# L3 UI Runbook — Phase 2E ✅ Sealed

> Phase 2E sealed: 2026-07-04 (c929142)
> GitHub Actions: run 28692470181, status success

## Start the UI

```bash
cd workspace/AutoAD-Researcher
uv run --extra ui streamlit run src/autoad_researcher/ui/app.py
```

Opens at `http://localhost:8501`.

## Sidebar（所有页面通用）

- **当前任务**：显示人类可读任务名（首次在研究助手中对话后自动生成）
- **任务摘要**：一句话描述
- 若尚未生成：显示"未命名研究任务"，提示前往研究助手
- **高级信息折叠**：run_id / 制品目录 / CLI 复现命令

## Pages

### 1. 运行配置
填写 API Key，系统自动生成运行 ID。"重新生成"按钮创建新 ID。高级配置折叠。

### 2. 预检执行器
执行 `l3-preflight`。通过后显示下一步 checklist 和终端复现命令。

### 3. 制品浏览器
按 Stage 展示产物文件。每个 Stage 有中文说明和 ⭐ 推荐文件。

### 4. 执行监控
先显示执行摘要（单元完成数、GPU 设备），原始 JSON 折叠。

### 5. 最终审阅
三栏结论：补丁与管线 / 执行完成度 / 科学结论。含人话解释。

### 6. 研究助手（Phase 2E）
三种模式：
- **意图澄清**：描述研究想法，系统整理成可审计研究意图草案
- **运行解释**：基于 artifacts 解释当前运行状态
- **下一步建议**：基于实验结果建议下一轮方向

**自动任务命名（Phase 2E 新增）**：
- 首次在研究助手中发送消息后，系统自动调用 LLM 生成任务名和摘要
- 任务名要求：中文 6-14 字或英文 3-8 词，具体表达研究目标
- 保存为 `runs/{run_id}/ui_chat/task_profile.json`
- 不会包含 run_id、API key 或路径
- 生成失败时自动降级为"未命名研究任务"
- 任务名显示在侧边栏和页面 1/2/6 的顶部 banner

研究意图草案、确认、Pipeline 输入准备、HITL Gate Status 和 Approval Gates 功能与 Phase 2D 保持一致。

## Browse an Existing Run
侧边栏输入 `run_l3_bottle_001` 等已有 Run ID 浏览历史制品。

## Limitations
- 研究助手不执行 pipeline，只写 approval JSON
- 真实 L3 仍需在终端手动运行，并设置 `AUTOAD_L3_REAL_EXECUTION_ALLOWED=1`
- Web 登录、多用户、数据库和远程审批不在 Phase 2D 范围内
