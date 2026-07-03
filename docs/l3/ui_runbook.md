# L3 UI Runbook — Phase 2C

## Start the UI

```bash
cd workspace/AutoAD-Researcher
uv run --extra ui streamlit run src/autoad_researcher/ui/app.py
```

Opens at `http://localhost:8501`.

## Pages

### 1. 运行配置
填写 API Key，系统自动生成运行 ID。高级配置（数据集路径、Provider URL）折叠。

### 2. 预检执行器
执行 `l3-preflight`（不调用 LLM，只检查配置）。通过后显示下一步 checklist。

### 3. 制品浏览器
按 Stage 展示产物文件。每个 Stage 有中文说明和 ⭐ 推荐文件。

### 4. 执行监控
先显示执行摘要（单元完成数、GPU 设备），原始 JSON 折叠。

### 5. 最终审阅
三栏结论：补丁与管线 / 执行完成度 / 科学结论。含人话解释。

### 6. 研究助手（Phase 2B）
三种模式：
- **意图澄清**：描述研究想法，系统整理成可审计研究意图草案
- **运行解释**：基于 artifacts 解释当前运行状态
- **下一步建议**：基于实验结果建议下一轮方向

研究意图草案：
- 在“意图澄清”模式聊完后，点击“生成研究意图草案”
- 系统要求 LLM 返回严格 JSON，并校验为 `ResearchIntentDraft`
- 草案保存为 `runs/{run_id}/ui_chat/intent_draft.json`
- 可读摘要保存为 `runs/{run_id}/ui_chat/intent_draft.md`
- clarification 桥接输入保存为 `runs/{run_id}/ui_chat/clarification_input.json`

人工确认：
- 有 `intent_draft.json` 后，页面会显示“确认采用 / 需要修改 / 驳回”
- 点击后写入 `runs/{run_id}/approvals/intent_confirmation.json`
- Phase 2C 起，pipeline 在 `patch_planner` 前强制要求 `decision=approved`

Pipeline approval gates：
- Patch Plan Approval 写入 `runs/{run_id}/approvals/patch_approval.json`
- Real Execution Approval 写入 `runs/{run_id}/approvals/run_approval.json`
- UI 仍然只写 JSON，不执行 patch-plan、patch-apply、runner-execute 或 stage3-acceptance
- `runner_execute` 仍额外要求 `AUTOAD_L3_REAL_EXECUTION_ALLOWED=1`

Phase 2C enforce 行为：
- 无 `intent_confirmation.json` 或未 approved → `patch_planner` blocked
- 无 `patch_approval.json` 或 `confirmed_by_user=false` → `patch_applicator` blocked
- 无 `run_approval.json`、`confirmed_by_user=false` 或真实执行环境变量缺失 → `runner_execute` blocked
- 已存在的 resume artifact 不能绕过 approval gate
- 每次检查都会写 `{stage_dir}/approval_gate_report.json`

安全限制：
- 只提供解释和建议，不修改代码，不执行 L3
- 不声称科学提升，除非 final_facts 支持
- 聊天记录保存在 `runs/{run_id}/ui_chat/chat_transcript.jsonl`
- intent draft 与 confirmation 是 UI 审计材料，不进入 Stage 3 artifact chain
- 不保存 API Key；误输入的 `sk-*` 样式内容会被脱敏或拒绝

## Browse an Existing Run
侧边栏输入 `run_l3_bottle_001` 等已有 Run ID 浏览历史制品。

## Limitations
- 研究助手不执行 pipeline，只写 approval JSON
- Approval gate enforcement 已在 pipeline stage 入口生效
- 真实 L3 仍需在终端手动运行，并设置 `AUTOAD_L3_REAL_EXECUTION_ALLOWED=1`
- Web 登录、多用户、数据库和远程审批不在 Phase 2C 范围内
