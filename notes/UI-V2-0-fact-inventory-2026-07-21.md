# UI-V2-0 最新主线事实盘点

基线：`main@61615f01202fc088559ff01bbbd3391b18f2361b`，其父提交为
`6a6a52f02db3ec1c462e949dcffb390b8774a150`。本盘点只读当前主线和两个旧 UI
分支，不把旧分支作为 Git 基线，也不改变 API、投影、WebSocket 或实验/报告状态。

## 1. 当前应用结构

- `frontend/src/App.tsx` 是单一 React Shell，通过 `PageId = chat | experiment | report`
  切换三个工作区，没有独立前端路由。
- App 当前直接维护 run、消息、排队消息、Source、Jobs、Evidence、Intent Summary、
  artifacts、实验确认、Toast 和页面选择状态；UI-V2 应保持这些状态归属和现有
  竞态保护，只重做呈现层与局部交互组件。
- 当前主线的 `LeftSidebar` 使用 48/160 px `width` 动画和 hover 展开；`PlusMenu`、
  `ConfigModal`、实验确认、Toast 和 ReportPage 主要使用内联样式，尚未引入主题
  Provider、语义 token 或组件 primitives。

## 2. 研究对话功能合同

### 已存在的功能

- Session：列出、创建、重命名、归档、恢复、删除和切换任务；由 `TaskMenu` 和
  `getRuns/createRun/renameRun/archiveRun/restoreRun/deleteRun` 驱动。
- 对话：读取 transcript；发送消息；服务端 WebSocket 回填 assistant delta、完成
  状态、工具行和 Toast；失败时保留可见错误消息。
- 资料：文件上传、Source intake、Source 删除；右侧 Inspector 展示 Sources、Jobs、
  Evidence、Intent Summary 四个 tab；Evidence 同时保留不可用解析项及其错误信息。
- 队列：活动对话期间继续发送会进入 queued 状态；可恢复到输入框；失败后暂停队列，
  不把排队消息当成已发送消息。
- 异步实验草案：`sendChat` 返回 `experiment_task` 后打开确认面板；支持 execution mode、
  已完成采集的本地/GitHub 仓库、主指标补录和过期摘要错误。
- 开发者详情：开发模式下有 mock 资料流和 Toast 演示；它不是生产研究状态合同。

### 当前交互表面

- Dialog：API 配置 `ConfigModal`；实验任务 `ExperimentTaskConfirmation`；删除资料、
  删除 Session 和已确认草案恢复使用浏览器原生 `window.confirm`。
- Popover/展开层：`LeftSidebar` hover 展开、`TaskMenu` Session history、`PlusMenu`
  上传菜单、右 Inspector 的开发者详情、artifact 内容折叠。
- Toast：全局最多保留 3 条，来源包括任务操作、上传失败、实验确认、WebSocket 和
  开发 mock；当前使用不可中断的 `toastIn` keyframes，后续应换成连续 transition。
- 发送按钮、上传按钮、任务操作和确认按钮都有真实 disabled/失败边界；动画不得延迟
  请求提交、状态展示或队列排空。

## 3. Experiment Observatory 合同

数据入口是 `getExperimentProjection(runId, sessionId?, signal?)`，请求由
`ExperimentPage` 用 `AbortController`、请求序号、refresh scope version 和 300 ms
防抖保护。失败时保留上一份有效 projection，并显示可见错误。

`ExperimentProjection` 的字段边界如下，UI 不得根据 attempts 或文字自行推导动作资格：

- 选择：`selection_status` 为 `no_session | selected | ambiguous`；ambiguous 使用
  `session_candidates` 让用户明确选择。
- Session：`session_id`、`task_ref`、`task_hash`、`status`、`execution_mode`、
  `readiness_status`、`readiness_blockers`、`environment_status`、`baseline_status`、
  evaluation contract 引用/hash、budget、时间戳。
- 研究对象：`input_task`、`summary`、`idea_tree`、`attempts`、`candidates`、
  `candidate_inventory_status`、`cognitive_commits`、`champion_status` 和 `champion`。
- 动态：`activity`、`activity_limit`、`activity_truncated`、`activity_scan_truncated`。
- 可审计引用：`developer_refs`，仅作为开发者详情，不参与普通用户动作判断。
- 服务端动作：`actions.candidate_confirmations` 与
  `actions.candidate_promotions`。当前页面只在 Session 为
  `approve_each_step` 时显示受限审批区，并调用 `confirmCandidate` 或
  `promoteCandidate`；没有任意命令、仓库或执行表单。

当前页面由 Idea Tree、研究动态、详情/Attempt 列表三列组成；Detail 展示 Idea、执行
事实、OutcomeCard、科学评价、Assessment reconciliation、证据引用及“在研究助手中
讨论”。WebSocket 只触发刷新，不承载第二份实验状态。

## 4. Report Workspace 合同

`ReportPage` 当前已不是简单 Markdown 查看器。它加载：报告列表、最新 content-ready、
最新 created、选定版本的 state、digest、Markdown、Evidence、Discussion 和 Proposal。

### 版本和状态

- `ReportManifest`：`report_id`、`version`、`generation_status`、`review_status`、
  `format_status.markdown/html/pdf/bundle`、Source snapshot SHA、Facts SHA。
- `ReportState`：生成/审阅/格式状态、Pipeline jobs、retry_count、last_error、
  `available_artifacts`。
- 当前选择优先保留用户已选版本，否则选择最新可读版本，再退回最新创建版本；当有
  更新版本但当前仍显示旧可读版本时，页面会提示正在生成的新版本。
- 只有 `generation_status === content_ready` 才加载 Digest、正文、Evidence、Discussion
  和 Proposals；pending/failed/unavailable 不能混成同一种“报告失败”。

### 用户操作

- 版本选择、刷新、返回对话。
- HTML 新窗口、PDF 下载、Bundle 下载，均由 `available_artifacts` 决定是否显示。
- Review 当前提供 `accept` 和 `needs_more` 两个显式入口，并保留用户评论。
- `REQUEST_HUMAN` Proposal：创建、查看 validation errors、确认转交、拒绝；状态包含
  `DRAFT`、`READY_FOR_CONFIRMATION`、`CONFIRMED`、`REJECTED`、`SUPERSEDED`、
  `HANDED_OFF`，handoff 类型独立显示。
- Evidence 使用折叠详情显示 evidence kind、ID、摘要、Attempt/Idea 关联和 SHA。
- Report Discussion 只在 `content_ready` 且 `report.md`、`report_validation.json` 可用
  时启用；消息、turn、失败状态从真实报告接口读取。

UI-V2 的 Report 布局可以重新设计为“版本/目录、正文/Digest、状态/审阅/Proposal/下载”
的工作区，但不得增加 Experiment PipelineJob、复制执行控制面或改变这些 API 状态。

## 5. 当前测试矩阵

主线 fixture Playwright（`playwright.config.ts` 会排除 fullstack）共 16 项：

- `experiment-confirmation.spec.ts`：3 项，仓库 source_id 绑定、取消不调用确认接口、
  backend summary conflict 保持面板。
- `experiment-observatory.spec.ts`：11 项，持久化快照、Champion 审批、Detail 刷新、
  invalid/available scientific assessment、Session facts、Idea 关联过滤、动态扫描
  截断、WebSocket 合并刷新、刷新失败保留旧快照、Session 选择竞态。
- `report-page.spec.ts`：2 项，报告状态/指标/HTML、Review 和版本隔离的 human Proposal。

真实 fullstack Playwright（`playwright.fullstack.config.ts`）共 2 项并串行运行：

- 真实 API 下明确执行仓库选择、任务确认、binding/session/pipeline job 文件断言。
- 真实报告生成、Bundle ready/入口、Review 持久化、REQUEST_HUMAN handoff、刷新恢复，
  并断言人工跟进前后 `pipeline_jobs.jsonl` 不变。

主线组合 CI 还执行 `bash scripts/verify.sh`、frontend lint、production build、fixture
Playwright 和 fullstack Playwright。视觉基准、主题回归和交互动效回归目前只存在于旧
UI 分支，不能假定它们已经是新主线门禁。

## 6. 旧 UI 分支可移植性

### 可选择性移植

- `frontend/src/theme/*`：system/light/dark 选择、持久化和语义 token 的起点；需按当前
  main 的真实组件重新接入，不整体覆盖 `App.tsx`。
- `frontend/src/components/ui/{AppButton,IconButton,Surface,StatusBadge,EmptyState}.tsx`：
  无业务语义 primitives，可作为 V2-1 起点。
- `usePresence`：Popover/Toast/Modal 的 presence 模式；迁移前要重新核对当前组件的
  关闭和焦点边界。
- `globals.css` 中的语义颜色、排版、glass、focus、reduced motion 和 transform/opacity
  交互模式；只移植被当前页面验证需要的规则。
- `theme.spec.ts`、`visual-baselines.spec.ts`、`motion-accessibility.spec.ts` 的测试
  思路和 fixture 结构；选择器、页面状态和视口必须按新 main 重新编写。

### 不可整体迁移

- `App.tsx`、`ExperimentPage.tsx`、`ReportPage.tsx`、`LeftSidebar.tsx`、`Sidebar.tsx`
  和 `TaskMenu.tsx`：旧分支基于更早的数据/报告合同，整体 cherry-pick 会覆盖当前
  Report Agents、projection/actions 和真实 fullstack 流程。
- 旧 `SettingsPage`：不恢复已删除的实验 Settings 入口；新 UI 只渲染当前主线的
  projection/actions 和报告工作流。
- 旧视觉测试图片和旧报告页面 fixture：只能作为视觉参考，不能当作新主线的事实状态。

旧分支相对当前 main 的差异规模为：

- `ui/apple-dual-theme-2026-07-20`：27 个文件，941 行新增、216 行删除。
- `ui/interaction-motion-audit-2026-07-21`：35 个文件，1490 行新增、249 行删除。

它们继续作为只读参考，不 merge、不整体 rebase、不整体 cherry-pick。

## 7. UI-V2 冻结边界

- 保留 `App.tsx` 的状态归属、`ExperimentPage` 的读取/竞态策略、Report API 调用和
  WebSocket 失效通知。
- UI-V2-1 只先落地主题 token 和无业务 primitives；UI-V2-2 再重建 Shell。
- 动效优先作用于 Shell、Popover、Modal、Toast、版本切换和 Inspector 开合；Idea Tree
  节点、Activity 行、指标数字、报告正文和实验状态本身不做装饰性运动。
- 不引入大型动画库；预定式动效优先 CSS transition、`@starting-style` 或 WAAPI；只有
  真实拖拽/动量手势才重新评估 JS spring。
