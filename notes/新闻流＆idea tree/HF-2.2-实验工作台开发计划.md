# HF-2.2：实验工作台 (Experiment Observatory) 开发计划

## 1. 目标

将左侧“设置”入口改造成实验工作台，展示当前实验的 Session、Idea Tree、Attempt、科学评价和研究动态。

本功能是只读观察面，不增加实验控制动作，不修改 Coordinator、AttemptExecution、Finalizer、Promotion 等核心逻辑。

## 2. 总体原则

```text
现有权威 Store / Artifact
        ↓
只读投影
        ↓
ExperimentPage
        ↑
现有 WebSocket 只做失效通知
```

### 2.1 不能违反的边界

1. 不新增第二套权威状态。
2. 不把 `task_ref` 当研究目标；当前 Starter 会写入固定的 `input_task.yaml`。
3. 不调用会写 Artifact 的科学评价服务来响应 GET。
4. 不从当前最终 Idea Tree 反推历史 mutation。
5. 不从 CandidateSnapshot 直接读取不存在的科学结论字段。
6. 不按字典第一个值、时间或目录顺序选择 Champion 或 Session。
7. 不为了页面首版增加 `ExperimentSessionStore.list_sessions()`。
8. 不修改现有 WebSocket envelope，不增加第二条 broadcast 路径。
9. 底层 enum 保持精确，用户界面通过确定性映射显示中文。

Arbor 的成熟做法是用持久化 Idea Tree / RunState 生成紧凑的只读监控状态，原始事件独立保留；MLflow 的 Run metadata、metrics、inputs、Artifact 分层用于参考 AutoAD 的执行事实和科学评价分层。两者都是设计参考，不直接复制代码。

## 3. 提交一：导航、配置入口与空页面

### 3.1 改动

| 文件 | 改动 |
|---|---|
| `frontend/src/lib/types.ts` | 将 `PageId` 的 `settings` 页面改为 `experiment` |
| `frontend/src/components/LeftSidebar.tsx` | 设置入口改名为“实验工作台” |
| `frontend/src/App.tsx` | 渲染 ExperimentPage，保留顶部 ConfigModal |
| `frontend/src/components/ExperimentPage.tsx` | 新建工作台布局和空状态 |
| `frontend/src/components/SettingsPage.tsx` | 保留现有实验配置表单，供工作台嵌入 |

### 3.2 配置职责不能混淆

当前 `ConfigModal` 只负责通用 API Key、Base URL、Model；当前 `SettingsPage` 负责实验 Provider、实验 Model、实验 API Key、Reasoning Effort、Cycle、Turn、Timeout、文献搜索和 Idea 新颖性检查。

工作台增加“实验配置”按钮，复用 `SettingsPage` 的表单和 `saveExperimentConfig`。顶部齿轮继续打开 `ConfigModal`。不要重写第二套表单，也不要声称两个组件已经共用全部字段。

### 3.3 空状态

- 无 `runId`：请先创建一个研究任务。
- 无 Session：实验尚未启动，请先在研究助手中确认实验任务。
- 多个 Session：发现多个实验 Session，请明确选择；首版不偷偷选择。

### 3.4 验收

- 实验工作台入口可打开。
- 通用 API 配置仍可打开和保存。
- 实验配置字段未丢失。
- Chat、Report 不回归。

## 4. 提交二：后端只读投影

### 4.1 新增文件

| 文件 | 职责 |
|---|---|
| `src/autoad_researcher/assistant/v2/experiment_projection.py` | Pydantic 投影 Schema 和装配器 |
| `src/autoad_researcher/server/routes/experiment_projection.py` | 投影 GET API |
| `tests/test_v2_experiment_projection.py` | 投影测试 |
| `src/autoad_researcher/server/main.py` | 注册新路由 |

### 4.2 投影选择状态

新增 API Schema 使用明确的选择状态：

```python
selection_status: Literal["no_session", "selected", "ambiguous"]
session: SessionProjection | None = None
session_candidates: list[SessionSummary] = Field(default_factory=list)
summary: SessionStats | None = None
```

`session_id=None` 时只读发现 `experiments/sessions/*.json`：

- 0 个：`no_session`；
- 1 个：加载该 Session；
- 多个：`ambiguous`，不加载任意一个。

指定 `session_id` 时准确加载，不回退。

### 4.3 研究目标

按 `session.task_ref` 安全读取现有 `input_task.yaml`，使用已有 `InputTask` Schema 校验。

前端展示：

```text
user_idea ?? request
```

同时投影 baseline、dataset、primary_metrics、constraints。`task_ref` 只作为开发者引用。

### 4.4 Attempt 科学事实分层

每个 Attempt 需要分别读取：

```text
attempts/{attempt_id}/outcome_card.json
attempts/{attempt_id}/scientific_assessment.json
attempts/{attempt_id}/assessment_reconciliation.json
```

含义分别是：

- OutcomeCard：执行、协议和指标解析事实；
- ScientificAssessment：科学比较结论；
- AssessmentReconciliation：两种事实的权威边界。

缺少科学评价时保留执行事实，并显示“科学评价尚未物化”。GET 不得调用 `assess()` 或 `effective_assessment()`。

### 4.5 Champion 选择

严格遵循：

```text
Session.evaluation_contract_sha256
  → current_summary_for_session()[contract_hash]
  → ChampionPointer + CandidateSnapshot
  → CandidateSnapshot.attempt_id
  → 读取已有科学评价
```

无评价合同、无对应 Pointer、Candidate 不属于当前 Session 或 Artifact 无效时，显示“暂未产生”。

### 4.6 Idea Tree 和 Activity

- Idea Tree 只展示当前节点、状态、父子关系、insights 和 Attempt 引用。
- `experiment.idea_tree.mutated` 只显示“Idea Tree 已更新”和 revision。
- 不从 mutation receipt 推断历史节点或剪枝原因。
- Activity 映射使用确定性逻辑，不调用 LLM。

### 4.7 API

```text
GET /api/runs/{run_id}/experiment/projection?session_id={session_id}
```

| 情况 | HTTP |
|---|---:|
| run 不存在 | 404 |
| 指定 Session 不存在 | 404 |
| 无 Session | 200 |
| 多 Session 未选择 | 200 |
| 正常 | 200 |

接口只读，不创建目录、不写文件、不追加事件。

### 4.8 验收

- Pydantic Schema 可实例化。
- 无 Session、单 Session、多 Session 行为正确。
- 研究目标来自 InputTask，不显示 `input_task.yaml`。
- 执行事实和科学评价分层。
- Champion 使用当前评价合同精确选择。
- GET 前后 Artifact 内容和文件修改时间不变。

## 5. 提交三：工作台数据展示

### 5.1 顶部概览

显示研究目标、baseline、dataset、Session 状态、环境状态、Baseline 状态、Idea 统计、Attempt 统计、预算、Champion 和异常提醒。

研究目标不得使用 `session.task_ref`。

### 5.2 中文状态

底层值不变，增加确定性中文映射：

```text
QUEUED       → 等待运行
RUNNING      → 运行中
COMPLETED    → 已完成
FAILED       → 运行失败
TIMED_OUT    → 运行超时
CANCELLED    → 已取消
LOST         → 运行状态丢失
```

Idea 状态按当前 `IdeaNodeStatus` 映射。未知值显示“未知状态（原始值：XXX）”。

### 5.3 Idea Tree

使用 React 嵌套列表；`PRUNED` 默认折叠；Attempt 作为计数徽章；点击节点打开详情；不加入当前 Schema 没有的状态。

### 5.4 Activity Feed

按后端投影的 ActivityCard 展示当前可靠事实。Idea mutation 不显示具体历史节点变更。WebSocket 只触发重新请求投影，不直接插入卡片。

### 5.5 Attempt 详情

分为：

1. 执行事实：runtime status、command、retry、resource、OutcomeCard；
2. 科学评价：ScientificAssessment；
3. 权威边界：AssessmentReconciliation。

评价 Artifact 缺失时不生成文件、不猜结论。

### 5.6 验收

- 目标、状态和科学结论来源正确。
- 中文界面可理解，原始值仍可追踪。
- 缺少 metrics 或科学评价时仍展示已有事实。
- Idea、Attempt、Activity 点击详情正确。
- 普通模式隐藏内部路径和 ID。
- 讨论按钮只预填，不自动发送。

## 6. 提交四：WebSocket 失效通知刷新

### 6.1 后端

不修改 `ws.py`，不扩展 envelope，不增加 broadcast。

### 6.2 前端

- `App.tsx` 收到 `experiment.*` 时递增刷新计数；
- `ExperimentPage` 对刷新计数做 300ms 防抖；
- 防抖后重新请求投影；
- 重连 replay 多个事件时合并为一次请求；
- run/session 切换时防止旧请求覆盖新状态。

### 6.3 验收

- 实验状态变化后工作台能刷新。
- WS envelope 保持不变。
- 不根据 payload 维护实验状态。
- 重连不重复显示事件卡片。
- 现有 Chat、Sources、Jobs、Evidence、Toast 行为不变。

## 7. 统一测试与交付流程

每个提交编码前：

1. 读取当前源码、测试和配置，核对精确标识符；
2. 编写对应测试；
3. 更新当天 `notes/YYYY-MM-DD.md`；
4. 运行 `bash scripts/verify.sh`；
5. 通过后运行 `bash scripts/verify_and_push.sh "<message>"`；
6. 确认 `git status --short --branch`、`git log --oneline -3` 和 GitHub Actions。

不得把审阅报告中的示例字段当成现有代码事实，也不得在未验证参考项目实现的情况下声称“直接复用”。
