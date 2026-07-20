# 开发计划 R6：ReportPage 交互式前端（对应 PR-R3）

## 1. 目标

原地升级真实路径 `frontend/src/components/ReportPage.tsx`，先完成报告版本、生成状态、Markdown/HTML、摘要、证据和下载。Discussion 只接入已验证内容，不首版实现复杂工作台。

## 2. 设计依据

| 来源 | 参考内容 | 本项目处理 |
|---|---|---|
| Arbor WebUI/Companion | 研究视图与只读 Companion 分离 | 参考交互边界 `[REFER]` |
| AiScientist TUI | Overview/Events/Logs/Conversation 分离 | 只参考信息分层，不复制 TUI |
| AutoAD 当前 `ReportPage.tsx` | 现有加载和 Markdown 渲染 | 原地升级 |
| AutoAD `MarkdownContent` | Markdown 展示 | 直接复用 |

## 3. 首版组件

建议先保持组件数量小：

```text
frontend/src/components/ReportPage.tsx
frontend/src/components/report/ReportToolbar.tsx
frontend/src/components/report/ReportStatusSummary.tsx
frontend/src/components/report/ReportViewer.tsx
frontend/src/components/report/EvidenceCard.tsx
frontend/src/components/report/ReportDownloadMenu.tsx
```

`ReportWorkspace`、复杂 ProposalCard 和 DiscussionPanel 在后续阶段接入，避免前端先于后端事实契约膨胀。

## 4. 首版布局

桌面端采用固定比例双栏或单栏折叠，不做拖拽：

```text
┌─────────────────────────────────────────────┐
│ 状态摘要 | Report v001 | HTML | 下载         │
├──────────────────────────┬──────────────────┤
│ Markdown/HTML 报告       │ 摘要/证据卡       │
│ 表格和章节               │ 可选 Discussion   │
└──────────────────────────┴──────────────────┘
```

窄屏使用普通上下布局；不首版实现底部抽屉、复杂移动端状态机或 PDF viewer。

## 5. 状态和摘要

顶部明确显示：

```text
generation_status
review_status
format_status
工程/执行/科学状态
report_id / version
Champion
停止原因
```

Digest 作为摘要卡渲染，不伪装成一条写入 transcript 的系统消息。摘要必须带 `report_id` 和 Facts hash，避免用户看到旧版本摘要。

讨论启用条件：

```text
Snapshot 已冻结
+ Markdown 存在
+ Validator 通过
```

PDF 失败时显示 PDF 不可用，但不禁用报告阅读和 Discussion。

## 6. EvidenceCard

报告正文中的证据链接使用 `evidence_id`，点击后请求：

```text
GET /api/runs/{run_id}/reports/{report_id}/evidence/{evidence_id}
```

卡片只展示后端解析和大小限制后的摘要、artifact 类型、Attempt/Idea ID、相对定位和 SHA。前端不接受服务端返回的绝对路径作为可点击 URL。

## 7. 交互流程

```text
进入 Report
→ 读取 latest-content-ready
→ 查询 latest-created 的生成状态
→ 读取 manifest/status
→ 若 report.md 可用则展示
→ digest 作为摘要卡
→ 证据链接按 report_id 请求
→ 用户下载当前固定版本制品
```

后续 Discussion/Proposal 流程：

```text
报告内容可审阅
→ DiscussionPanel 解锁
→ Discuss / Propose
→ ProposalCard 展示
→ 用户确认后显示“已转交”，而不是“已执行”
```

## 8. 验收

- [ ] 实际 `components/ReportPage.tsx` 读取新版 API。
- [ ] 报告未完成时显示生成步骤和错误原因。
- [ ] queued/running 的最新创建版本不会遮挡仍可读的 content-ready 版本。
- [ ] PDF 失败不阻断 Markdown/HTML 阅读。
- [ ] HTML 只下载或新窗口打开，不通过 `dangerouslySetInnerHTML` 注入当前 DOM。
- [ ] 版本切换后摘要、内容和下载均绑定同一 `report_id`。
- [ ] EvidenceCard 不能读取未登记 artifact。
- [ ] 失败和不可比较 Attempt 在报告中可见。
- [ ] 首版不依赖拖拽、抽屉或 PDF viewer。
- [ ] Proposal 的确认状态和实验执行状态明确区分。

## 9. 不做什么

- 不做报告编辑。
- 不把报告对话写入主 Chat transcript。
- 不在前端计算科学结论、delta 或 Champion。
- 不首版做复杂图表交互和响应式布局抽象。
