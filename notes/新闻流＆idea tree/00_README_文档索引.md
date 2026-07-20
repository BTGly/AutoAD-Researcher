# 实验工作台 (Experiment Observatory) 开发计划 — 文档索引

> 范围：仅覆盖前端「实验工作台」只读投影层。
> 不覆盖：实验 Agents 核心逻辑修改、实验控制面、聊天功能变更。

## 参考覆盖说明

本计划吸收以下外部项目的可视化/UI 模式：

- **Arbor** — Dashboard / RunState projection / Idea Tree / replay showcase
- **MLflow Tracking UI** — Experiment → Run → Metric/Artifact 层次
- **MiMo/OpenCode** — 任务树 + 详情面板布局
- **Claude Code internals** — 会话侧栏 + Evidence 展示模式
- 现有 **AutoAD-Researcher** — Session Store / IdeaTree / AttemptStore / OutcomeCard / CognitiveCommit / CandidateRegistry / WebSocket

## 外部参考复用等级

| 标签 | 含义 | 本计划使用 |
|------|------|-----------|
| `[REFER]` | 仅借鉴架构或设计模式，不复制源码 | Arbor Dashboard、MLflow Tracking |
| `[ADAPT]` | 算法主体可用，适配 AutoAD 数据模型 | CoordinatorContextBuilder 的 ContextPack 读取模式 |
| `[REUSE]` | 直接复用现有 AutoAD 代码 | SessionStore、IdeaTreeStore、AttemptStore、WebSocket |

### 复用矩阵

| 来源 | 等级 | 仓库地址 | 参考路径 | 落点 |
|------|------|----------|----------|------|
| Arbor Dashboard | `[REFER]` | `/root/autodl-tmp/AI4S/references/research-automation/Arbor/` | `src/arbor/dashboard/` `src/arbor/run_state.py` | 实验工作台三栏布局、Session 概览、Idea Tree 节点状态视觉 |
| Arbor RunState | `[REFER]` | 同上 | `src/arbor/run_state.py` | 只读投影装配模式：权威 Store → 投影 → 前端渲染 |
| MLflow Tracking UI | `[REFER]` | `/root/autodl-tmp/repos/mlflow/` | `mlflow/tracking/` `mlflow/server/js/src/` | Experiment→Run→Metric/Artifact 层次、Run 详情面板 |
| MiMo/OpenCode | `[REFER]` | `/root/autodl-tmp/AI4S/references/coding-agents/MiMo-Code/` | — | 任务树嵌套列表、详情面板「点击展开」模式 |
| AutoAD CoordinatorContextBuilder | `[ADAPT]` | `projects/AutoAD-Researcher/` | `src/autoad_researcher/experiment/coordinator.py` | 投影装配器复用其 Store 读取和组装模式 |
| AutoAD WebSocket polling | `[REUSE]` | 同上 | `server/routes/ws.py` + `assistant/v2/event_service.py` | 现有 events.jsonl → WS 路径，不额外 broadcast |

### 关键路径

| 路径 | 说明 |
|------|------|
| `projects/AutoAD-Researcher/frontend/src/` | 所有前端源码 |
| `projects/AutoAD-Researcher/src/autoad_researcher/` | 所有后端源码 |
| `projects/AutoAD-Researcher/src/autoad_researcher/experiment/` | 实验核心：session、idea_tree、attempt_store、finalizer、cognition、coordinator、promotion |
| `projects/AutoAD-Researcher/src/autoad_researcher/server/routes/` | API 路由：ws.py、runs.py、chat.py 等 |
| `projects/AutoAD-Researcher/src/autoad_researcher/assistant/v2/` | V2 助手：orchestrator、event_service、research_intent_summary 等 |

## 文档列表

| 文件 | 内容 |
|------|------|
| `00_README_文档索引.md` | 本文件：范围、参考复用、文档列表、开发顺序 |
| `01_导航与页面外壳.md` | PageId 改 `experiment`、LeftSidebar、ConfigModal 保留、ExperimentPage 空 shell |
| `02_后端只读投影.md` | `experiment_projection.py` 投影装配器、API、数据合同 |
| `03_工作台数据展示.md` | Session 概览、Idea 嵌套树、Activity Feed 时间线、Detail Drawer、「在研究助手中讨论」、开发者详情 |
| `04_WebSocket实时刷新.md` | WS envelope 补 `event_id`/`created_at`、`experiment.*` 事件处理、防抖重请求、重连恢复 |

## 推荐开发顺序

```text
提交一（01 → 导航与空页面）
  → 提交二（02 → 后端只读投影）
    → 提交三（03 → 工作台真实数据）
      → 提交四（04 → WebSocket 实时刷新）
```

原因：

- 没有页面 shell，无法展示任何内容；
- 没有后端投影 API，前端无法获取实验数据；
- 没有前端组件，后端接口无法验证；
- 实时刷新是最后接入的锦上添花功能。

完成后根据真实使用反馈决定是否引入：D3 树、回放动画、全局助手侧栏、独立的报告 Agent。
