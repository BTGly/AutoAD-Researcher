# 实验工作台 (Experiment Observatory) 开发计划 — 文档索引

> 范围：只读展示现有实验状态；并保留已存在的、服务端从冻结工件派生的受限显式审批入口。
> 不覆盖：实验 Agents 核心逻辑、任意命令执行、任意合并、聊天协议和新的历史事件协议。

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

审批动作不属于投影或 WebSocket 状态：仅在 Session 的 `approve_each_step` 模式下展示，参数和目标由服务端根据已冻结的候选与工件校验，页面不接收任意命令、路径或合并目标。

根据本地固定版本 Arbor 源码对照，本计划参考其“紧凑 UI 状态 + 原始事件分离”机制；这里是设计参考，不断言 AutoAD 与 Arbor 的实现细节完全等价：

- `/root/autodl-tmp/AI4S/references/research-automation/Arbor/src/cli/run_state.py`
  只保存仪表盘需要的紧凑状态，原始事件仍保留在事件文件中；
- `/root/autodl-tmp/AI4S/references/research-automation/Arbor/src/webui/session_source.py`
  将持久化 Session 映射成只读 snapshot；
- `Arbor/docs/web-ui.md` 将浏览器监控层建立在持久化状态之上。

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

以下版本和文件是在本地参考快照中实际核对过的记录。采用范围只表示设计借鉴，不表示复制源码；本地路径只供当前环境定位，不能作为运行时依赖。

| 仓库 / URL | 固定版本 | 许可证 | 本地路径 | 精确参考文件 | 采用范围 | 不采用内容 |
|---|---|---|---|---|---|---|
| [Arbor](https://github.com/RUC-NLPIR/Arbor) | `4f8c5c2e8d4b8d238ae911da486240e1ba95f4ca` | Apache-2.0 | `/root/autodl-tmp/repos/Arbor` | `src/cli/run_state.py`、`src/webui/session_source.py`、`docs/web-ui.md` | 参考紧凑 UI 状态、持久化状态到只读 snapshot、原始事件与 UI 分离 | 不复制 Arbor 的事件模型、Web UI 或运行时代码 |
| [MLflow](https://github.com/mlflow/mlflow) | `77769e5f3022ba92da4f5a9a9cba7b31d0ede758` | Apache-2.0 | `/root/autodl-tmp/repos/mlflow` | `mlflow/entities/run_info.py`、`mlflow/entities/run_data.py`、`mlflow/entities/run_inputs.py` | 参考运行元数据、指标、输入和 Artifact 的观测分层 | 不引入 MLflow Tracking Server、数据库或其 API |
| [MiMo-Code](https://github.com/XiaomiMiMo/MiMo-Code) | `42e7da3d51dba1129cd3abfa214e29f7385924a3` | MIT | `/root/autodl-tmp/repos/mimo-code` | `README.md`（checkpoint、context reconstruction、task progress） | 参考持久化 checkpoint 和任务进度恢复；工作台只读快照不依赖浏览器内存 | 不引入其 SQLite、Agent 调度、上下文重建或 TypeScript 代码 |
| AutoAD 当前代码 | `ec5237f540ef0f76778fbe7704a22596642dd2d8`（当前工作树另有未提交改动） | MIT | `/root/autodl-tmp/AI4S/projects/AutoAD-Researcher` | `src/autoad_researcher/experiment/`、`src/autoad_researcher/assistant/v2/event_service.py`、`src/autoad_researcher/server/routes/ws.py` | 直接读取本项目已有 Store、Artifact、事件 replay/polling | 不为了页面新增第二套状态、Store 公共枚举接口或 WS broadcast |

## 5. 暂不做

- 不删除实验配置能力；
- 不新增第二套实验状态机；
- 不新增 `ExperimentSessionStore.list_sessions()`；
- 不增加 `event_id` / `created_at` 到 WS envelope；
- 不从当前最终 Idea Tree 反推历史 mutation；
- 不在 GET 请求中生成科学评价 Artifact；
- 不引入 D3、Cytoscape 或完整历史 replay 动画。
