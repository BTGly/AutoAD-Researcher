# HF-2.2：实验工作台 (Experiment Observatory) 开发计划

## 目标

将左侧中间「设置」页改造为「实验工作台」，只读展示实验进展。

## 架构原则

1. **不新增第二套权威状态** — 数据来自现有 Store (Session/Idea/Attempt/Outcome/Cognition/Champion)，前端只做只读投影
2. **Event 只作变化通知** — 事件不携带完整科研数据；收到事件后重新请求投影，而非从事件 payload 推断
3. **不修改实验 Agents 核心逻辑** — Coordinator、AttemptExecution、Finalizer、Promotion 等不因本功能修改
4. **复用现有 WebSocket** — 不新增 broadcast；继续使用 events.jsonl → ws.py polling 路径
5. **首版不引入大型可视化框架** — Idea Tree 用 React 嵌套列表或简单 SVG，不用 D3/Cytoscape

---

## 提交一：导航与空页面

### 改动清单

| 文件 | 改动 |
|------|------|
| `frontend/src/lib/types.ts` | `PageId`: `"chat" | "settings" | "report"` → `"chat" | "experiment" | "report"` |
| `frontend/src/components/LeftSidebar.tsx` | ITEMS 第三项改 `{ id: 'experiment', icon: '🔬', label: '实验工作台' }` |
| `frontend/src/App.tsx` | 路由条件 `page === 'settings'` → `page === 'experiment'`；移除 `<SettingsPage>` 渲染 |
| `frontend/src/components/ExperimentPage.tsx` | **新建** — 三栏布局 shell + 空状态 |
| `frontend/src/components/SettingsPage.tsx` | 移出主路由，保留文件供顶部 `ConfigModal` 复用 |

### ExperimentPage 空状态

- 无 Session → 居中显示「实验尚未启动。请先在研究助手中确认实验任务。」
- 无 runId → 居中显示「请先创建一个研究任务。」

### 验收

- 左侧导航显示「实验工作台」，点击后打开空页面
- 设置仍可通过顶部齿轮打开 ConfigModal
- 设置功能未丢失

---

## 提交二：后端只读投影

### 新增文件

| 文件 | 职责 |
|------|------|
| `src/autoad_researcher/assistant/v2/experiment_projection.py` | 只读投影装配器 |

### 投影装配器

输入：

```python
run_dir: str
session_id: str
```

输出：

```python
@dataclass
class ExperimentProjection:
    session: ExperimentSession | None            # Session 基础信息
    summary: SessionSummary                       # 统计摘要
    idea_tree: IdeaTreeProjection | None          # Idea 树投影
    attempts: list[AttemptProjection]             # Attempt 列表
    cognitive_commits: list[CognitiveCommit]       # 认知提交列表
    champion: ChampionInfo | None                 # 当前最优方案
    activity: list[ActivityCard]                  # 科研动态卡片
    developer_refs: DeveloperRefs                 # 内部引用（仅开发者模式）
```

#### SessionSummary

- `status`, `readiness_status`, `environment_status`, `baseline_status` — 直接读 `ExperimentSession`
- `idea_count` — 读 `IdeaTreeStore`
- `attempt_summary` — `ExperimentAttemptStore.list_for_session()`，按 `runtime_status` 分组计数
- `budget` — 读 `ExperimentSession.budget`
- `budget_consumed` — 读 `CognitiveBudget` 记录；无数据 → `None`
- `champion_summary` — 读 `CandidateRegistry.current_summary_for_session()`；无数据 → `None`

#### IdeaTreeProjection

- 保留 `IdeaNode` 全部现有字段
- 不加 schema 中不存在的 status
- 每个节点携带 `attempt_summary`（链接 attempt 的数量和状态分布）

#### AttemptProjection

| 字段 | 来源 |
|------|------|
| `attempt_id`, `attempt_purpose`, `runtime_status` | `ExperimentAttempt` schema |
| `command_plan_summary` | `ExperimentAttempt.command_plan` |
| `retry_of`, `retry_count`, `max_retries` | `ExperimentAttempt` |
| `outcome_card` | 读 `OutcomeCard`；不存在 → `None` |
| `failure_code`, `failure_classification` | `ExperimentAttempt` + `OutcomeCard`
| `related_idea_ids` | `IdeaTree` 中 `attempt_refs` 反向查找 |

#### ActivityCard

由 **event + event 引用的权威 artifact** 组合生成：

| Event | 附加读取 | 卡片标题 |
|-------|----------|----------|
| `experiment.session.created` | ExperimentSession | 实验 Session 已创建 |
| `experiment.idea_tree.created` | IdeaTree | Idea 树已初始化 |
| `experiment.idea_tree.mutated` (add_child) | IdeaTree → 读取新增节点 | 新 Idea：{mechanism} |
| `experiment.idea_tree.mutated` (mark_status → PRUNED) | IdeaTree → 读取剪枝节点 | 方向已停止：{mechanism} |
| `experiment.idea_tree.mutated` (mark_status → SUPPORTED) | IdeaTree → 读取节点 | 证据支持：{mechanism} |
| `experiment.attempt.created` | ExperimentAttempt + IdeaTree | 实验排队：{purpose} |
| `experiment.attempt.queued` | ExperimentAttempt | 实验加入队列 |
| `experiment.attempt.running` | ExperimentAttempt | 实验开始运行 |
| `experiment.attempt.finalized` | ExperimentAttempt + OutcomeCard | 实验完成：{scientific_effect} |
| `experiment.attempt.retry_queued` | ExperimentAttempt | 重试已排队 |
| `experiment.cognitive_commit.appended` | CognitiveCommit | 认知更新：{verdict} |
| `experiment.coordinator.exploratory_cycle.committed` | — | 探索周期完成 |
| `experiment.coordinator.compact_cycle.committed` | — | 收敛周期完成 |
| `experiment.champion.promoted_and_merged` | CandidateRegistry | Champion 已更新 |

- 未知 event type → 研究者模式下忽略，开发者模式下保留原始 JSON
- Activity 生成使用确定性映射，不调用 LLM

#### DeveloperRefs

- `run_id`, `session_id`
- `event_ids` 列表
- `artifact_paths`
- `pipeline_job_ids`
- `prompt_versions`
- 原始 `events.jsonl` 路径

### 新增 API

```text
GET /api/runs/{run_id}/experiment/projection?session_id={session_id}
```

- 返回 `ExperimentProjection` JSON
- 只读，不写入任何文件
- session_id 不存在 → 404
- run_id 不存在 → 404

### 测试

覆盖：

1. 没有 ExperimentSession
2. 一个 Session
3. 多个 Session（需用户选择）
4. Session 已创建但未准备环境
5. Baseline 运行中
6. Attempt queued / running / completed
7. Attempt failed / timed_out / lost
8. OutcomeCard 缺少 metrics
9. NON_COMPARABLE OutcomeCard
10. IdeaTree 含 pruned / inconclusive / merged
11. CognitiveCommit 多轮
12. 没有 Champion
13. 有 Candidate 但没有 Promotion
14. 已有 Champion
15. 未知 event type
16. 投影接口不得写入任何实验文件

### 验收

- curl 请求投影接口返回正确 JSON
- schema 字段值与现有 Store 一致
- 所有测试通过

---

## 提交三：工作台真实数据展示

### ExperimentPage 三栏布局

```
┌─────────────────────────────────────────────────────────┐
│  Session 概览                                           │
│  任务 · 状态 · Idea 数 · Attempt 统计 · 预算 · Champion  │
├─────────────────┬──────────────────────┬────────────────┤
│  Idea Tree      │  研究动态              │  详情面板       │
│  嵌套列表       │  Activity Feed        │  Detail Drawer  │
│                 │  时间线卡片             │                │
└─────────────────┴──────────────────────┴────────────────┘
```

### 顶部：Session 概览

| 组件 | 数据来源 |
|------|----------|
| 研究任务 | `ExperimentSession.task_ref` |
| Session 状态 | `ExperimentSession.status` |
| Environment / Baseline 状态 | `ExperimentSession.environment_status`, `baseline_status` |
| Idea 数量 | `ExperimentProjection.summary.idea_count` |
| Attempt 统计 | `summary.attempt_summary`（queued/running/completed/failed 等计数） |
| 当前 Champion | `summary.champion_summary` |
| 预算 | `summary.budget` / `summary.budget_consumed` |
| 异常提醒 | 如有 failed/lost attempt 或有 readiness_blockers |

不使用百分比进度条。

### 左侧：Idea Tree

- React 嵌套列表组件，依据 `parent_id` / `children` / `depth` 渲染
- 节点状态视觉映射：

| Status | 表现 |
|--------|------|
| DRAFT | 空心节点 |
| REVIEWED | 实心，无额外标记 |
| READY | 实心 + 时钟图标 |
| RUNNING | 动态边框 |
| SUPPORTED | 绿色实心 |
| NOT_SUPPORTED | 降低透明度 |
| INCONCLUSIVE | 虚线边框 |
| PRUNED | 灰色，默认折叠 |
| MERGED | 菱形节点 |

- 每个节点显示 Attempt 计数徽标，不展开所有 Attempt 为独立节点
- 当前 Champion 路径高亮
- 已剪枝分支默认折叠
- 点击节点 → 右侧详情面板

### 中间：研究动态

- 垂直时间线，最新在上
- 每张卡片：图标 + 标题 + 摘要 + 时间
- 点击卡片 → 右侧详情面板
- 事件驱动刷新（提交四接入后生效）
- 只展示科研相关事件（不展示: Worker claim, Job lease, prompt hash, PID, 绝对路径）

### 右侧：详情面板

点击 Idea 节点：
- 假设、机制、观测目标、研究轴、预期成本
- 证据引用
- 父子关系
- 关联 Attempts 列表（purpose + status）
- 关联 CognitiveCommits（观察 + 判断 + 下一步）
- Insights
- 剪枝或保留原因

点击 Attempt：
- purpose、runtime_status
- command_plan 摘要
- OutcomeCard（指标、scientific_effect、primary_delta、protocol_valid）
- failure_code、failure_classification
- retry 关系
- resource usage

点击 Activity 卡片：
- 事件时间、类型
- 关联 Idea / Attempt / Commit
- 用户可读说明
- 相关 artifact 引用

### 「在研究助手中讨论」按钮

每个详情面板底部：

- 点击 → 切换到 Chat 页面
- 预填草稿问题，包含明确的 Idea ID / Attempt ID / artifact 引用
- 用户确认后发送
- 不得自动发送

### 开发者详情

复用现有「开发者详情」折叠模式：

- 默认隐藏 run_id、session_id、event enum、Job ID、artifact path、prompt version
- 展开后只读显示

### 验收

- Idea Tree 父子关系正确
- 所有状态映射来自当前 schema
- OutcomeCard 缺指标时仍能展示失败事实
- 普通模式不显示原始路径
- 开发者详情可以查看原始引用
- 点击 Idea/Attempt 打开正确详情
- 「在研究助手中讨论」不会自动发送消息
- 无 Session 时显示空状态

---

## 提交四：WebSocket 实时刷新

### 后端改动

| 文件 | 改动 |
|------|------|
| `src/autoad_researcher/server/routes/ws.py` | WebSocket 消息 envelope 补充 `event_id` 和 `created_at` 字段（向后兼容） |

**不修改**所有实验模块的 `append_event()` 调用。继续保持 `events.jsonl` → ws.py polling 路径。

### 前端改动

| 文件 | 改动 |
|------|------|
| `frontend/src/hooks/useWebSocket.ts` | 扩展 `WSMessage` 类型，支持 `experiment.*` 事件 |
| `frontend/src/App.tsx` | `onWsMessage` 增加 `experiment.*` 处理分支 |
| `frontend/src/components/ExperimentPage.tsx` | 监听 WS 事件 → 防抖 → 重新请求投影 |

### 前端更新策略

收到 `experiment.*` 事件时：

1. 合并短时间内的重复刷新请求（300ms 防抖）
2. 重新请求 `GET /api/runs/{run_id}/experiment/projection`
3. 使用返回的权威状态更新页面

不：
- 在前端重写完整实验状态机
- 根据 event payload 直接拼接 UI
- 在 WebSocket 重连后重复显示事件

### WS 重连恢复

- ExperimentPage mount 时请求初始投影
- WebSocket 重连后重新请求投影（已有自动重连机制）
- 不依赖浏览器内存维持页面状态

### 验收

- 后台运行实验时，工作台自动更新
- 切换 run / session 后只显示对应数据
- WebSocket 重连后不重复显示事件卡片
- 关闭再打开页面后恢复最新状态
- 现有 source / job / assistant / toast 事件不受影响

---

## 完成四个提交后的暂缓项

以下内容不由本计划覆盖，根据真实使用反馈决定：

- D3 / Cytoscape 可交互树
- 拖动或编辑 Idea
- 独立 Assistant Sidebar
- 全流程动画回放
- 新的 Event Bus 或数据库
- 自动修改实验方向
- 在实验页面直接运行命令
- 前端自行判断 KEEP/DISCARD
- LLM 自动生成动态卡片

---

## 验收标准

```
研究助手
→ 用户确认实验任务
→ ExperimentSession 创建
→ 后端自动运行实验
→ 实验工作台实时更新
→ Idea Tree 显示真实演化关系
→ Attempt 显示真实运行状态
→ OutcomeCard 显示真实科学结论
→ 失败和重试不会消失
→ 当前 Candidate / Champion 可追踪
→ 用户可以返回研究助手讨论某一项
```

同时满足：

- 不增加第二套实验状态
- 不修改实验 Agents 的科研判断规则
- 不重复广播事件
- 不依靠关键词猜测科研状态
- 不根据文件时间猜测当前 Session
- 不为首版引入大型可视化框架
- 现有测试继续通过
- 新增实验工作台专项测试
