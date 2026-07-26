# AutoAD UAT Round3 修复交接文档

**交接日期：** 2026-07-26  
**适用项目：** `projects/AutoAD-Researcher`  
**UAT 依据：** `参考/测试问题汇总/2026-07-26_AutoAD_UAT_round3.md`  
**关联记录：** `参考/测试问题汇总/2026-07-26_AutoAD_UAT_round2.md`  
**当前基线：** `18bb9b5 feat: gate experiment confirmation on readiness`  
**当前状态：** 本地存在未提交修改；本交接文档不表示 round3 已修复或已验收通过。

## 1. 交接结论

本轮不是单一的前端刷新问题，而是“资料接入 → 解析 → 仓库候选 → 用户授权 → readiness → 实验确认”之间的状态边界没有被正确表达。

已经确认的关键原则：

1. 用户明确提供且可唯一解析的本地路径，可以在同一轮自动完成 Source 登记和资料 Job 排队，不应要求用户第二句话说“登记”。
2. 后台解析结果写盘后，必须通过已有 Run WebSocket/EventStore 链路通知前端；用户不应通过发送下一条消息或手动刷新才能看到状态变化。
3. 代码仓库可以被系统自动识别为“执行仓库候选”，但不能因为候选唯一就自动成为执行仓库。
4. 执行仓库会影响环境准备、代码修改、训练和实验运行，必须获得用户明确授权。
5. `admitted_execution_repository_source_ids` 表示已经通过用户授权和 admission 的执行仓库，不能拿来表示待用户选择的候选仓库。
6. LLM 负责理解用户语言和提出候选动作；确定性代码负责校验 Source ID、候选集合、当前状态、授权、幂等和副作用。

本次交接的修复方向是“候选识别与执行授权分离”，不是“唯一仓库自动绑定”。

## 2. UAT 实际问题

### 2.1 明确路径需要两段式登记（P1）

**实际行为：**

- 用户第一句话提供绝对路径；
- 系统只将路径加入只读会话上下文；
- 不创建正式 Source，也不排队解析 Job；
- 用户必须第二次说明“登记”或类似意图，才会正式登记。

**用户感受：** 用户已经给出了明确资料，却不知道为什么系统没有推进。

**根因：** 当前设计把“路径识别/上下文附加”和“正式 Source 登记”交给了两个阶段，并依赖后续 LLM action 才完成 promote。对于可唯一解析的显式路径，这种拆分不符合资料接入预期。

**目标行为：**

```text
明确且唯一可解析的路径
  → 路径检查
  → Source 登记
  → 资料 Job 排队
  → 前端显示登记/解析状态
```

保留以下边界：

- 路径不存在：明确报错；
- 路径有多个匹配：要求用户确认具体路径；
- 用户声明类型与结构证据冲突：先请求类型确认，不要静默登记错误类型；
- 路径解析成功不代表用户已授权该仓库执行代码。

### 2.2 解析完成后前端不自动刷新（P1）

**实际行为：**

- `dataset_manifest` 等后台 Job 完成；
- Source registry 中的 `intake_status` 已更新为 `ok`；
- 前端 Sources 面板仍显示“已登记，待采集”；
- 用户发送下一条消息或手动刷新后才看到新状态。

**已确认根因：**

- `update_source_intake_result()` 负责写盘；
- 写盘后没有与 Source intake 状态对应的前端通知事件；
- 前端已有 WebSocket，但当前主要根据 `job.completed`、`artifact.created`、`evidence.updated` 等事件触发侧栏刷新；
- Source intake 状态变更没有稳定的专用事件契约。

**目标行为：**

```text
Worker 更新 Source intake 状态
  → 持久化 registry
  → 追加 Source intake 状态变更事件
  → WebSocket 推送
  → 前端重读 Sources / Jobs / readiness
```

还需要防止旧的刷新请求晚返回并覆盖新状态。不能只增加事件名而忽略前端请求竞态、WebSocket 重连和页面刚打开时的状态恢复。

### 2.3 执行仓库绑定需要用户说内部话术（P1）

**实际行为：**

- 已有已采集完成的代码仓库；
- readiness 仍要求 `admitted_execution_repository_source_ids` 非空；
- 系统不会自然地询问用户是否使用该仓库；
- 用户需要说出类似“用 AnomalyCLIP 仓库作为执行仓库”的内部化表达，才可能触发绑定。

**错误方向：**

不能修复为：

```text
候选仓库数量 == 1
  → 自动写入 executable role
  → 自动进入 admission
```

唯一性只能消除“用户需要在多个候选中选择哪一个”的歧义，不能消除“用户是否授权执行”的决策。

**目标行为：**

系统应以用户可理解的方式询问：

> 已完成解析的代码仓库是 `/path/to/repository`。是否使用它作为本次实验的执行仓库？执行仓库会用于后续环境准备、代码修改和实验运行。

用户明确确认后，系统才允许写入执行角色和生成 binding。

### 2.4 readiness 循环阻塞（P1）

**实际行为：**

- readiness 需要 `admitted_execution_repository_source_ids`；
- 该列表又依赖 LLM 触发特定 repository admission action；
- 用户在唯一已采集仓库面前看到“无已绑定执行仓库”；
- 界面没有告诉用户下一步应该确认什么。

**根因：** 候选仓库、已授权执行仓库和 readiness blocker 被压缩成同一个字段语义。

**目标状态：**

| 状态 | 含义 | 是否允许执行 |
|---|---|---:|
| `no_repository_candidate` | 没有检测到代码仓库候选 | 否 |
| `repository_pending` | 仓库仍在采集或解析 | 否 |
| `awaiting_repository_confirmation` | 有候选，但等待用户确认 | 否 |
| `repository_admission_failed` | 用户已确认，但 attestation/adapter admission 失败 | 否 |
| `ready` | 用户授权、admission、资料和任务合同均通过 | 是 |

“等待用户确认”必须是可操作的状态，不应渲染成“无可用仓库”。

### 2.5 确认弹窗简陋、信息过载（P2）

当前 `frontend/src/components/ExperimentTaskConfirmation.tsx` 同时承担：

- 展示实验任务；
- 让用户选择执行仓库；
- 读取内部 admission 投影；
- 处理主指标 readiness；
- 执行最终交接。

并且组件大量使用内联 `style={{}}`，下拉选择直接暴露了内部状态投影。

目标不是做无关的视觉重构，而是拆开用户决策：

1. 先用自然语言/确认卡完成“是否授权这个仓库作为执行仓库”；
2. 用户授权完成后，实验确认弹窗只展示已经冻结的执行仓库；
3. 实验确认弹窗不再让用户从 `admitted_execution_repository_source_ids` 中手选内部 ID。

## 3. 当前实现边界

### 3.1 已存在的正确原则

`src/autoad_researcher/assistant/v2/execution_repository.py` 已明确表达：

- 执行目标不能从 Source kind、目录名或 checkout 自动推断；
- 必须先持久化用户授权的 role assignment；
- admission 需要结合 repository attestation 和 ExecutorAdapter evidence；
- 后续执行使用冻结的 `ExecutionRepositoryBinding`。

这条原则必须保留，不能为了让 readiness 变成 `ready=true` 而绕过。

### 3.2 当前需要重点核对的代码

- `src/autoad_researcher/assistant/v2/orchestrator.py`
  - 显式路径处理；
  - session context 与正式 Source promote；
  - 当前轮路径去重和 Source action。
- `src/autoad_researcher/ui/session_context.py`
  - 显式路径提取；
  - 本地路径上下文附加。
- `src/autoad_researcher/ui/sources.py`
  - `update_source_intake_result()`；
  - Source registry 持久化；
  - local path Source 登记和 Job 创建。
- `src/autoad_researcher/worker/main.py`
  - `dataset_manifest`、`local_repo_acquire`、`repo_summarize` 等 Worker 分支；
  - Job terminal event 的统一出口。
- `src/autoad_researcher/assistant/v2/event_service.py`
  - `append_event()` 和 EventStore 事件记录。
- `src/autoad_researcher/server/routes/ws.py`
  - Run WebSocket 事件推送。
- `src/autoad_researcher/server/ws_manager.py`
  - 同一 Run 的连接广播和重连边界。
- `frontend/src/hooks/useWebSocket.ts`
  - 事件去重、重连和 event id。
- `frontend/src/App.tsx`
  - `onWsMessage()`；
  - `refreshSidebar()`；
  - `refreshTaskConfirmation()`；
  - 多请求旧响应覆盖新状态的风险。
- `src/autoad_researcher/assistant/v2/task_bridge.py`
  - `ExperimentTaskReadiness`；
  - `evaluate_experiment_task_readiness()`；
  - `admitted_execution_repository_source_ids` 的当前计算。
- `frontend/src/components/ExperimentTaskConfirmation.tsx`
  - 执行仓库选择和实验确认交互。
- `src/autoad_researcher/assistant/prompt_registry.py`
  - Decision/Reply 提示词中的 action scope、permission、confirm transition 和自然语言确认边界。

## 4. 目标状态模型

候选和授权必须分开建模：

```text
用户提供路径
  ↓
路径解析与只读结构检查
  ↓
Source 登记
  ↓
资料 Job / repo attestation / adapter analysis
  ↓
repository candidate
  ↓
用户确认“是否使用该仓库执行”
  ↓
RepositoryRoleAssignment(role="executable")
  ↓
ExecutionRepositoryAdmission
  ↓
ExecutionRepositoryBinding
  ↓
ExperimentTaskReadiness.ready
  ↓
环境准备 / 代码修改 / 实验运行
```

必须区分：

- `repository_candidates`：系统根据资料事实识别出的可选仓库；
- `admitted_execution_repository_source_ids`：已获得用户授权且通过 admission 的仓库；
- `execution_repository_binding`：后续执行使用的冻结仓库事实。

不允许用一个字段同时表达三者。

## 5. 提示词工程方案

### 5.1 提示词职责

提示词只负责：

- 判断用户当前是在提供资料、询问资料、确认候选，还是拒绝/改选；
- 将自然语言映射到当前挂起的确认请求；
- 生成面向用户的解释和下一步提示。

提示词不能：

- 自行创建用户授权；
- 根据“唯一”生成 executable role；
- 生成未经候选集合校验的 Source ID；
- 将“开始实验”泛化成“确认某个未点名仓库”；
- 把内部字段名暴露给用户。

### 5.2 单候选仓库

上下文向模型提供人类可读候选信息：

- 仓库显示名；
- 路径或用户可识别的来源描述；
- 当前采集/attestation/adapter 状态；
- 使用该仓库的实际影响；
- 当前是否存在挂起的仓库授权请求。

模型可以识别以下类型的确认：

- “是”
- “可以，就用这个”
- “用 AnomalyCLIP”
- “就用刚才那个仓库”

控制层必须把这些确认绑定到当前唯一挂起候选，而不是让模型直接决定任意 `source_id`。

### 5.3 多候选仓库

当存在多个候选时，不能接受无指向性的“是”。系统应列出人类可区分的信息并要求选择：

```text
我发现两个可用于执行的代码仓库：
1. ...
2. ...
请告诉我本次实验使用哪一个。
```

如果用户的名称或路径匹配不到候选，或者匹配多个候选，继续澄清；不要静默选择。

### 5.4 拒绝和改选

- 用户拒绝：保留仓库 Source 和解析结果，但不写 executable role；
- 用户要求另一个仓库：重新建立候选确认请求；
- 用户确认了仓库 A 后又改选仓库 B：不能静默复用 A 的 binding；
- 用户取消：清理挂起确认状态，但不删除资料或证据。

## 6. 修复执行顺序

### 阶段 0：保护当前状态

- 继续使用当前工作区，不执行 destructive Git 操作；
- 区分上一轮未提交修改与新修复；
- 先保存本交接文档和当天日志；
- 记录专项测试基线。

当前工作区已有未提交修改，至少涉及：

```text
src/autoad_researcher/assistant/prompt_registry.py
src/autoad_researcher/assistant/v2/execution_repository.py
src/autoad_researcher/assistant/v2/job_service.py
src/autoad_researcher/assistant/v2/orchestrator.py
src/autoad_researcher/assistant/v2/task_bridge.py
src/autoad_researcher/ui/session_context.py
src/autoad_researcher/ui/sources.py
src/autoad_researcher/worker/main.py
frontend/src/*
tests/*
```

实际执行前必须再次读取 `git status --short`，不能假设这些修改已经完整或已经通过最终门禁。

### 阶段 1：明确路径同轮登记

- 在显式路径处理处区分“路径解析成功”和“用户声明类型冲突”；
- 对绝对路径和唯一相对路径，完成 Source/Job promote；
- 保留 Source/Job 幂等；
- 保留结构冲突确认；
- 增加跨轮复用和重复消息测试。

### 阶段 2：Source intake 实时刷新

- 在 Source intake 状态成功或失败写盘后，追加专用状态变更事件；
- 明确事件 payload 所需的精确字段，不能由前端猜测；
- 前端收到事件后刷新 Sources、Jobs 和 readiness；
- 增加 WebSocket 重连、事件重复、双标签页和旧响应覆盖测试；
- 保留页面初始加载时的 REST 状态重建。

### 阶段 3：候选仓库与已授权仓库解耦

- 将候选仓库作为只读投影；
- `admitted_execution_repository_source_ids` 只由持久化授权和 admission 结果产生；
- readiness 在“等待用户确认”时返回可操作 blocker；
- readiness 不得为了显示确认弹窗而伪造 admission；
- 确认接口必须以当前挂起候选为输入约束。

### 阶段 4：提示词和对话状态

- 增加仓库角色确认的 typed action/transition，沿用现有 Dialogue Gate，不新增第二套权限引擎；
- 向模型提供候选、挂起确认和影响说明；
- 由确定性控制层校验自然语言确认的候选对应关系；
- 明确确认、拒绝、改选、歧义和过期确认；
- 对未经授权的 code/experiment action fail closed。

### 阶段 5：确认 UI

- 先做仓库授权确认卡；
- 实验确认弹窗展示已冻结的执行仓库，不再显示内部 Source ID；
- 复用现有组件和 CSS 体系；
- 移除本组件不必要的内联 style；
- 检查键盘焦点、错误提示、200% 缩放和移动端布局。

### 阶段 6：专项、全量和真人 UAT

专项验证完成后再运行：

1. 相关 Python pytest；
2. `bash scripts/verify.sh`；
3. 前端 lint/build；
4. 相关 Playwright；
5. 真实 FastAPI/Vite 全栈流程；
6. round3 真人 UAT。

没有完成完整门禁和真人复验，不能宣布“闭环已修复”。

## 7. 验收矩阵

| 场景 | 预期结果 |
|---|---|
| 第一轮发送明确绝对路径 | Source 和 Job 立即创建，不要求第二句话 |
| 第一轮发送唯一可解析相对路径 | 与绝对路径一致；不会重复创建 |
| 路径不存在 | 明确显示不存在原因和下一步 |
| 路径存在但类型冲突 | 请求用户确认类型，不静默登记 |
| `dataset_manifest` 完成 | 前端自动显示 `intake_status=ok`，不发消息、不手动刷新 |
| WebSocket 重复事件 | 前端幂等，不重复追加 Job/Source |
| WebSocket 断线重连 | 通过 REST/事件状态恢复当前事实 |
| 只有一个仓库候选 | 询问用户是否授权，不自动绑定 |
| 用户确认唯一候选 | 写入授权，执行 admission，重新计算 readiness |
| 用户没有确认仓库就点击实验执行 | 不创建 execution binding，不创建环境执行 Job |
| 多个仓库候选 | 必须让用户明确选择 |
| 用户拒绝仓库 | Source 保留，但不进入 executable role |
| 用户改选仓库 | 旧 binding 不静默复用 |
| 页面刷新 | 恢复候选、待确认状态和已确认 binding |
| readiness blocker | 面向用户说明下一步，不展示内部字段作为操作要求 |
| 双标签页 | 同一 Run 的状态保持一致，不要求手动刷新 |

## 8. 明确禁止的修复

以下方案不得采用：

- `candidate_count == 1` 时自动写入 executable role；
- 将 `source_id` 直接作为用户确认结果；
- 让用户手填 `admitted_execution_repository_source_ids`；
- 让 LLM 直接生成未校验的 Source ID；
- 用固定中文关键词判断仓库授权；
- 用 `job.completed` 的泛事件替代所有 Source 状态契约；
- 用定时刷新掩盖后端事件缺失；
- 为了通过 readiness 测试而绕过 attestation、adapter 或用户授权；
- 通过 fixture 名称、固定路径或 AnomalyCLIP 特例实现；
- 把当前未提交修改当成已经完成的产品修复。

## 9. 成熟设计参考

本方案吸收以下本地参考中的共同机制，未照搬其代码：

- Arbor intake：先确认目标和完整 contract，再进入 coordinator；见 `../../references/research-automation/Arbor/skills/arbor-agent-setup-intake/SKILL.md`。
- Arbor launch：先暂存不可变计划，后续明确确认后才启动；见 `../../references/research-automation/Arbor/src/cli/intake/launch_tool.py`。
- Arbor HITL：在 executor dispatch 等真正产生副作用的阶段设置 review gate；见 `../../references/research-automation/Arbor/skills/arbor-agent-plugins-hitl-budget/SKILL.md`。
- DeepSeek-Reasonix：把模型解释和用户拥有的权限/计划决策分离；见 `../../references/coding-agents/DeepSeek-Reasonix/docs/TOOL_APPROVAL_MODES.md`。
- AutoAD 当前设计：候选事实与用户确认的正式任务参数分离；见 `docs/AutoAD_任务参数决策与来源协议.md`。
- AutoAD V2 对话设计：模型解释语言，确定性代码校验标识符、状态、权限、幂等和副作用；见 `docs/architecture/v2_dialogue_decision_and_state_projection.md`。

## 10. 当前未完成项

- round3 问题 1 至 4 尚未完成完整修复和真人复验；
- Source intake 专用事件及前端刷新竞态保护需要落地并测试；
- 仓库候选、用户授权、admission 和 readiness 需要重新解耦；
- 仓库确认提示词和对话状态尚未形成最终 schema/contract；
- 确认弹窗尚未完成交互和样式收敛；
- 完整 `scripts/verify.sh`、前端构建和最终推送尚未作为本轮修复的验收结论；
- round2 记录的“AI 无文件读取能力”是历史 P0，是否已由当前运行时能力解决，必须另行基于实际产品入口验证，不能仅凭本交接文档判定。

## 11. 接手后的第一步

接手者先执行以下只读检查：

```bash
cd /root/autodl-tmp/.autodl/autoad/AI4S/projects/AutoAD-Researcher
git status --short --branch
git log --oneline -3
sed -n '1,260p' docs/uat/2026-07-26_AutoAD_UAT_round3_交接文档.md
sed -n '1,260p' ../../参考/测试问题汇总/2026-07-26_AutoAD_UAT_round3.md
```

然后读取本交接文档列出的相关源码、测试和现有未提交 diff，再决定具体修改。不要先运行 destructive Git 操作，不要直接把唯一仓库写成执行仓库，不要跳过用户授权确认。
