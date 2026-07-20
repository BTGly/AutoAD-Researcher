# 实验工作台 (Experiment Observatory) 开发计划 — 文档索引

> 范围：只读展示现有实验状态。
> 不覆盖：实验 Agents 核心逻辑、实验控制面、聊天协议和新的历史事件协议。

## 1. 首版确定的架构

```text
现有权威 Store / JSON Artifact
        ↓
后端只读投影装配器
        ↓
ExperimentPage 展示快照
        ↑
现有 WebSocket 只发送“experiment.* 已发生”通知
```

首版不在前端维护第二套实验状态，也不根据 WebSocket payload 直接拼接科研结果。
事件只负责让投影失效；真实状态始终重新从 Store 和 Artifact 读取。

这采用 Arbor 已验证的“紧凑 UI 状态 + 原始事件分离”模式：

- `/root/autodl-tmp/AI4S/references/research-automation/Arbor/src/cli/run_state.py`
  只保存仪表盘需要的紧凑状态，原始事件仍保留在事件文件中；
- `/root/autodl-tmp/AI4S/references/research-automation/Arbor/src/webui/session_source.py`
  将持久化 Session 映射成只读 snapshot；
- `Arbor/docs/web-ui.md` 明确把浏览器监控层建立在持久化状态之上。

MLflow 的 `RunInfo`、`RunData`、`RunInputs`、Artifact 分层也只作为结构参考，不复制其代码。

## 2. 首版边界

### 2.1 Session 选择

当前 `ExperimentSessionStore` 只有准确 `session_id` 的 `load()`，没有 `list_sessions()`。
首版不为了页面增加 Store 公共接口，也不按文件时间、文件名或目录顺序暗中选择：

| 发现结果 | 投影状态 | 页面行为 |
|---|---|---|
| 没有 Session 文件 | `no_session` | 显示“实验尚未启动” |
| 恰好一个 Session 文件 | `selected` | 自动加载该 Session |
| 多个 Session 文件 | `ambiguous` | 显示待选择状态，不自动加载 |
| 指定不存在的 `session_id` | HTTP 404 | 不返回其他 Session |

多 Session 下拉选择器、任务修订模型和完整 Session Store API 延后到项目真正产生该需求时再设计。

### 2.2 历史事件

当前 `experiment.idea_tree.mutated` 事件通常只携带 mutation 名称和 tree revision；
批量 Coordinator 变更还可能统一记录为 `apply_mutations`，不携带目标节点、原因和新增节点 ID。
因此首版 Activity Feed 只能显示“Idea Tree 已更新 / 树版本”，不能伪造历史节点变化。

如果未来需要历史动画，必须先增加可重放的 mutation journal 或完整事件 payload，再单独设计 replay 功能。

### 2.3 科学评价

Attempt 展示必须区分：

1. `OutcomeCard`：执行与协议事实；
2. `scientific_assessment.json`：科学比较结论；
3. `assessment_reconciliation.json`：两者的权威边界。

GET 投影接口只能读取已经存在的 Artifact，不能调用会物化文件的评价服务。

## 3. 文档与开发顺序

| 文件 | 内容 |
|---|---|
| `00_README_文档索引.md` | 范围、事实边界、参考模式、开发顺序 |
| `01_导航与页面外壳.md` | 导航、实验配置入口、空页面 |
| `02_后端只读投影.md` | Pydantic 投影 Schema、只读装配、API、测试 |
| `03_工作台数据展示.md` | 中文状态映射、Idea Tree、Activity、Attempt 详情 |
| `04_WebSocket实时刷新.md` | 现有 WS 作为失效通知、投影防抖刷新 |
| `HF-2.2-实验工作台开发计划.md` | 上述四个提交的总计划 |

推荐顺序：

```text
提交一：01 导航与空页面
  → 提交二：02 后端只读投影
    → 提交三：03 工作台真实数据展示
      → 提交四：04 WebSocket 失效通知刷新
```

每个提交编码前必须重新读取当前分支的源码、测试和精确字段名；本计划中的新投影字段是待实现的 API 设计，不得被误当成现有模型字段。

## 4. 参考复用矩阵

| 来源 | 复用方式 | 本计划落点 |
|---|---|---|
| Arbor `src/cli/run_state.py` | 参考紧凑 UI 状态与原始事件分离 | 投影不是事件回放器 |
| Arbor `src/webui/session_source.py` | 参考持久化状态到只读 snapshot | 后端投影装配器 |
| Arbor `docs/web-ui.md` | 参考只读监控和刷新流 | WebSocket 只做通知 |
| MLflow Tracking UI | 参考 Run / Metric / Artifact 分层 | Attempt 执行事实与科学评价分层 |
| MiMo-Code `README.md` | 参考持久化 checkpoint、task progress | 不依赖前端内存或原始对话恢复 |
| AutoAD 当前 Store | 直接读取现有权威数据 | `SessionStore`、`IdeaTreeStore`、`AttemptStore` 等 |
| AutoAD 当前 WebSocket | 直接复用现有 polling/replay | 不增加 broadcast 和 envelope 字段 |

## 5. 暂不做

- 不删除实验配置能力；
- 不新增第二套实验状态机；
- 不新增 `ExperimentSessionStore.list_sessions()`；
- 不增加 `event_id` / `created_at` 到 WS envelope；
- 不从当前最终 Idea Tree 反推历史 mutation；
- 不在 GET 请求中生成科学评价 Artifact；
- 不引入 D3、Cytoscape 或完整历史 replay 动画。
