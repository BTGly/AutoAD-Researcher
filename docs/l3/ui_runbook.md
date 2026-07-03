# L3 UI Runbook — Phase 2A

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

### 6. 研究助手（Phase 2A）
三种模式：
- **意图澄清**：描述研究想法，系统整理为实验目标
- **运行解释**：基于 artifacts 解释当前运行状态
- **下一步建议**：基于实验结果建议下一轮方向

安全限制：
- 只提供解释和建议，不修改代码，不执行 L3
- 不声称科学提升，除非 final_facts 支持
- 聊天记录保存在 `runs/{run_id}/ui_chat/chat_transcript.jsonl`
- 不保存 API Key

## Browse an Existing Run
侧边栏输入 `run_l3_bottle_001` 等已有 Run ID 浏览历史制品。

## Limitations
- 研究助手不执行 pipeline，不触发 patch-apply/runner-execute
- 真实 L3 仍需在终端手动运行
