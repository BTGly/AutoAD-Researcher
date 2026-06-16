# AutoAD-Researcher 技术路线草案

> 用途：与导师讨论参赛方向、项目边界、MVP 和实现路线。  
> 当前版本：讨论稿 v4（2026-06-16）。  
> 本版重点调整框架分层，并新增 Idea Source Router、多 Agent 思想碰撞、用户选择和并行实验分支设计。赛题平台和测试环境尚未完全明确，本文不做确定性假设。

---

## 1. 项目定位

> **AutoAD-Researcher：面向异常检测的文献迁移与实验闭环智能体**

给定论文、方法想法、代码仓库和实验目标，系统先解析用户已经提供的材料，再针对真正缺失的信息与研究者沟通；随后生成一个或多个可验证的迁移方案，经用户选择后推进到实验规划、代码修改、受控执行、结果分析和多轮反思，最终形成可追溯结论。

本项目：

- 不是通用编程 Agent；
- 不是全自动 AI 科学家；
- 不是复刻完整 AutoSOTA；
- 不把 Deep Agents、AutoGen、CrewAI、LangGraph 或其他框架当作项目本体；
- 是一个 Human-in-the-loop 的异常检测 Dry Lab 闭环系统。

核心原则：

> **AutoAD Core 是领域闭环控制层；其他 Agent 框架、运行时和工具都是可替换组件。**

`run_id`、schema、artifact、审批、事件、实验分支、停止条件、白名单执行和科研有效性必须由 AutoAD Core 控制。

---

## 2. 真实科研闭环

项目不是“运行一次实验后结束”的线性流水线，而是：

```text
论文 / 方法想法 / repo / dataset / baseline / 资源约束
→ 输入接收与材料汇总
→ 论文和代码仓库解析
→ 基于已有材料的意图澄清
→ Idea Source Router
   ├── 用户明确 idea：直接分解实现方式
   ├── 用户 idea 模糊：多 Agent 讨论候选方案
   └── 用户要求明确：直接进入有效性检查
→ 用户选择单一方案或多个并行方案
→ 方法迁移可行性判断
→ 实验方案生成
→ 代码修改计划、patch 和工作量估计
→ 人工确认
→ 环境检测和实验运行时间估计
→ 受控实验执行
→ 日志、指标和科研有效性分析
→ 多 Agent 反思与候选下一轮分支
→ 用户确认下一轮方案
→ 下一轮实验
→ 达到停止条件
→ 无论成功、失败或部分成功，都生成报告和可视化
```

停止条件包括：

- 达到验证目标；
- 实验预算耗尽；
- 连续多轮没有有效改善；
- 科研有效性问题无法修正；
- 方法被判断不适合当前任务；
- 用户主动停止。

---

## 3. 项目边界与 MVP

### 3.1 第一阶段不做

- 不做全领域 AI 科学家；
- 不自动宣称发现 SOTA；
- 不同时支持大量论文、数据集和仓库；
- 不让 Agent 未经确认直接改代码或启动大规模训练；
- 不把 LLM 主观判断当作实验事实；
- 不只保存成功实验；
- 不第一版就做无限制多 Agent 自组织；
- 不让默认 shell / execute 绕过 AutoAD 白名单；
- 不为展示多 Agent 而强制所有任务进入讨论流程。

### 3.2 MVP 聚焦

```text
方向：视觉异常检测 / 工业缺陷检测
数据集：MVTec AD 的 1–2 个类别，或可公开演示的数据
baseline：PatchCore 优先，PaDiM / FastFlow 备用
任务：把一篇论文中的一个模块迁移到固定 baseline
目标：跑通一个最小但真实的多轮实验闭环
```

最低成功标准：

```text
解析论文和 repo
→ 基于已有材料澄清关键约束
→ 生成 1–3 个结构化候选 idea
→ 用户选择一个或多个方案
→ 输出迁移判断和实验计划
→ 生成 patch plan
→ 用户确认
→ 运行固定 benchmark 或 smoke test
→ 读取真实日志和指标
→ 给出有效性判断和下一轮建议
→ 无论成败都输出可追溯报告
```

---

## 4. 总体架构

```text
用户 / 研究者
  │
  ▼
Input Intake / Context Assembly
  - 创建 run_id
  - 保存原始输入
  - 汇总论文、repo、配置、数据集和资源信息
  │
  ▼
Paper Reader + Repository Reader
  - 解析论文方法、数据假设、指标和可迁移模块
  - 解析 repo 入口、配置、测试和可修改范围
  │
  ▼
Intent Clarifier
  - 汇总已知事实
  - 不重复询问已经提供的信息
  - 只追问影响实验决策的关键缺口
  │
  ▼
Idea Source Router
  ├── Direct User Idea
  ├── Idea Decomposition
  └── Multi-Agent Exploration
  │
  ▼
idea_candidates.json
  │
  ▼
用户选择：单线 / 多线并行 / 继续讨论 / 拒绝
  │
  ▼
Transferability Judge
  - 任务兼容性、迁移位置、风险和验证价值
  │
  ▼
Experiment Planner
  - baseline、dataset、metric、对照组、预算、停止条件
  │
  ▼
Code Patch Planner + Change Estimator
  - patch plan / diff
  - 代码修改和验证时间区间
  │
  ▼
Human Approval Checkpoint
  - approve / revise / reject
  │
  ▼
Environment Profiler + Runtime Estimator
  - GPU/CPU/RAM/数据规模
  - 训练和推理时间区间、置信度和资源风险
  │
  ▼
Runner / Sandbox
  - 应用批准的 patch
  - 白名单执行
  - 保存 stdout / stderr / metrics
  │
  ▼
Metrics Analyzer + Validity Supervisor
  - baseline 对比
  - 协议一致性、数据泄漏和证据充分性检查
  │
  ▼
Multi-Agent Reflection Team
  - Debug Agent
  - Method Critic
  - Experiment Designer
  - Validity Judge
  - Supervisor 汇总、去重、排序
  │
  ├────────→ 下一轮实验 ────────┐
  │                              │
  └────────→ 达到停止条件        │
                 │               │
                 ▼               │
Final Report + Visualization ◄───┘
```

### 4.1 为什么先解析，再澄清

系统不应在尚未阅读用户材料时立即发出通用问卷。

正确顺序：

```text
收集材料
→ 解析论文、repo、配置和历史实验
→ 汇总已知事实
→ 只询问真正缺失且影响决策的信息
```

Intent Clarifier 是 **基于证据的澄清器**，不是固定问卷。

---

## 5. 技术分层：不同框架解决不同问题

原先把大量框架放在同一个“后端候选”表中，容易形成错误理解。更准确的分层如下。

### 5.1 Foundation：系统地基

真正的地基是 AutoAD 自己的控制规则和事实层：

```text
AutoAD Core
├── run_id / workspace
├── Pydantic v2 schema
├── ArtifactStore
├── EventStore
├── StageResult / PipelineResult
├── PipelineController
├── Approval
├── Runner 安全边界
├── Scientific Validity Rules
└── Report Contract
```

地基要求：

- 不依赖任何单一 Agent 框架；
- 可以使用 SimplePipelineHarness 离线验证；
- 更换 Deep Agents、AutoGen 或 CrewAI 后仍然有效；
- 所有事实都可以通过 artifact 和 events 审计。

### 5.2 Intelligent Execution Backend：智能执行内核

这一层解决“一个具体 stage 如何调用模型、工具和文件完成任务”。

#### Deep Agents

适合：

```text
长程论文分析
repo 和文件系统任务
复杂 patch 规划
长日志分析
上下文管理
受控 subagents
```

在本项目中，DeepAgentsHarness 负责将选定 idea 推进为可执行规划和代码任务，但不负责定义科研事实和审批规则。

#### Pydantic AI

适合参考和验证：

```text
类型安全 Agent
结构化输入输出
validator / retry
依赖注入
模型无关接口
调用限制和可测试性
```

需要区分：

```text
Pydantic v2
  = AutoAD Core 的基础 schema 和校验工具

Pydantic AI
  = 可选的类型安全 Agent 执行框架
```

Pydantic AI 可以规范模型输出、校验字段和限制调用，但不能自动判断数据泄漏、评价协议变化和科研结论是否成立。这些仍由 AutoAD Core 的 Scientific Validity Supervisor 负责。

### 5.3 Multi-Agent Ideation & Deliberation：思想生成与讨论

AutoGen 和 CrewAI 对 AutoAD 最重要的价值不是替代整个 Core，而是：

> **作为 Idea Deliberation Engine，生成、质疑和比较多个候选科研方案。**

候选实现：

```text
AutoGenDebateBackend
CrewAIIdeationBackend
DeepAgentsIdeationBackend
```

#### AutoGen 适合探索的场景

```text
开放式讨论
动态选择发言者
观点冲突和反驳
多条方案产生
根据上下文继续追问
```

#### CrewAI 适合探索的场景

```text
固定角色专家团队
顺序或层级评审
Manager 汇总
清晰任务分工
结构化专家报告
```

初步建议：

```text
开放观点碰撞：优先做 AutoGen spike
固定角色评审：再做 CrewAI 对照 spike
长程代码和文件任务：继续使用 Deep Agents
```

### 5.4 Workflow Runtime：可靠流程运行时

LangGraph、Temporal 等不是科研地基，也不只是 Deep Agents 的“优化插件”。它们解决的是：

```text
程序中断后恢复
等待用户确认后继续
stage 重试
长时间 GPU 实验
并行实验分支
checkpoint
任务队列
多机器执行
```

关系应理解为：

```text
AutoAD Core
  定义流程语义、状态和规则

Workflow Runtime
  可靠地运行和恢复这些流程

Harness Backend
  执行具体智能任务
```

MVP 当前继续使用清晰的 Python PipelineController。只有出现真实的恢复、队列、并行和跨机器需求后，再接 LangGraph / Temporal。

### 5.5 Capability Subsystems：能力增强子系统

这些不是打地基时必须接入的组件，而是后续丰富功能和提高代码可用性的能力模块：

```text
OpenHands / SWE-agent
  - repo 导航、代码编辑、测试和沙盒参考

LlamaIndex / RAG
  - 论文资料、历史实验和知识检索

MinerU / MarkItDown
  - 论文和多格式文档解析

MLflow
  - 实验追踪和 artifact 管理增强

OpenTelemetry
  - traces、metrics 和 logs 可观测性

Visualization Libraries
  - 结果图、时间线、实验趋势和资源图
```

它们的优先级应由实际功能缺口决定，而不是作为 Core 前置条件。

---

## 6. Idea Source Router

Idea 并不只有一种来源。系统需要根据用户信息完整程度选择不同路线。

### 6.1 模式 A：用户没有明确 idea

示例：

```text
我想把这篇论文迁移到异常检测，但不知道从哪里开始。
```

进入：

```text
multi_agent_exploration
```

建议角色：

| 角色 | 核心问题 |
|---|---|
| Method Analyst | 论文的真实贡献和必要假设是什么？ |
| AD Domain Expert | 哪些机制符合异常检测设定？ |
| Architecture Agent | 可以插入 backbone、feature、memory bank 还是 scoring？ |
| Experiment Agent | 哪条路线最容易用最小实验验证？ |
| Skeptic / Validity Agent | 哪些方案存在泄漏、不公平或无法证伪的问题？ |
| Moderator | 汇总、去重和形成候选项 |

系统输出 1–3 个候选方案，并让用户选择：

```text
A. 先验证方案 A
B. 先验证方案 B
C. 多条路线并行跑低成本 smoke test
D. 继续讨论并生成其他方案
E. 暂停或拒绝迁移
```

### 6.2 模式 B：用户有 idea，但实现方式不唯一

示例：

```text
我想把论文里的新模块加入 PatchCore。
```

进入：

```text
idea_decomposition
```

系统讨论的重点不是“是否采用该 idea”，而是“如何实现”：

```text
方案 A：放在 backbone 中间层
方案 B：放在多尺度 feature fusion
方案 C：放在 embedding / projection 后
方案 D：只用于 anomaly score refinement
```

每个方案必须说明：理论依据、代码插入点、最小修改、风险、预计成本和最小验证实验。

### 6.3 模式 C：用户要求已经明确

示例：

```text
把模块 M 放在 layer2 和 layer3 特征拼接之后，保持 memory bank 和 anomaly score 不变。
```

进入：

```text
direct_user_idea
→ validity check
→ experiment planning
→ patch planning
```

系统不应为了展示多 Agent 而制造无意义讨论。

---

## 7. Idea 数据协议

所有框架必须使用同一套框架无关 schema，不能把 AutoGen 或 CrewAI 的内部消息当作最终结果。

建议模型：

```python
class IdeaCandidate(BaseModel):
    idea_id: str
    title: str
    description: str
    insertion_point: str
    rationale: str
    expected_benefits: list[str]
    implementation_risks: list[str]
    scientific_risks: list[str]
    minimum_experiment: str
    estimated_cost: Literal["low", "medium", "high"]
    confidence: float


class IdeaGenerationResult(BaseModel):
    mode: Literal[
        "direct_user_idea",
        "multi_agent_exploration",
        "idea_decomposition",
    ]
    candidates: list[IdeaCandidate]
    disagreements: list[str]
    recommended_candidate_ids: list[str]
```

建议框架无关接口：

```python
class IdeaGenerationBackend(ABC):
    @abstractmethod
    def generate_ideas(
        self,
        run_id: str,
        context: IdeaContext,
    ) -> IdeaGenerationResult:
        ...
```

可实现：

```text
DirectIdeaBackend
AutoGenDebateBackend
CrewAIIdeationBackend
DeepAgentsIdeationBackend
```

AutoAD Core 只消费 `IdeaGenerationResult`，不依赖框架内部对话结构。

---

## 8. AutoGen / CrewAI 对比 spike

不要直接把多个框架全部塞入主流程。先使用同一协议进行小规模对照实验。

### 8.1 AutoGen spike

最小团队：

```text
AD Expert
Method Expert
Skeptic
Moderator
```

输入：同一份 paper/repo/task artifact。  
输出：严格校验的 `IdeaGenerationResult`。

### 8.2 CrewAI 对照 spike

使用相同：

- 输入材料；
- 模型；
- token budget；
- 角色目标；
- 最大轮数；
- 停止条件；
- 输出 schema；
- 测试案例。

### 8.3 比较指标

```text
候选方案多样性
候选重复率
科研有效性
结构化输出成功率
证据引用完整性
用户可理解性
延迟
token 成本
可审计性
```

框架选择应基于 AutoAD 的真实 idea generation 任务表现，而不是宣传口号。

---

## 9. Artifact-first 与唯一事实源

对话历史、Agent memory 和框架虚拟文件系统都不能作为最终事实源。

```text
runs/{run_id}/
  input_task.yaml
  source_manifest.json
  paper_summary.json
  repo_summary.json
  clarified_task.json
  idea_context.json
  idea_candidates.json
  idea_selection.json
  idea_discussion_summary.json
  transfer_report.json
  experiment_plan.json
  patch_plan.json
  patch.diff
  change_estimate.json
  approval_patch.json
  environment.json
  runtime_estimate.json
  run_command.sh
  stdout.log
  stderr.log
  metrics.json
  validity_report.json
  reflection_candidates.json
  reflection_summary.json
  next_experiment_plan.json
  final_report.md
  figures/
  events.jsonl
  llm_calls.jsonl
```

原则：

- 控制逻辑尽量薄、清晰、可测试；
- 任务状态必须写入结构化 artifact；
- 后续 stage 重新读取 artifact，不依赖模型记忆；
- AutoGen/CrewAI 的自由讨论必须压缩成结构化结果和分歧摘要；
- 成功和失败都保留证据。

---

## 10. 关键模块设计

### 10.1 Paper Reader 与 Repository Reader

Paper Reader 输出：核心方法、模型组件、数据假设、标签需求、训练目标、数据集、指标、代码可用性、潜在迁移点和未解决问题。

Repository Reader 输出：repo 结构、训练/推理/评价入口、baseline 配置、可修改与禁止修改文件、测试命令，以及固定 evaluation script 的版本或指纹。

### 10.2 Intent Clarifier

输入：用户原始输入、`paper_summary.json`、`repo_summary.json` 和已知环境信息。

输出：已知事实、缺失信息、关键问题、`clarified_task.json` 和用户确认状态。禁止重复询问材料中已明确的信息。

### 10.3 Transferability Judge

判断：数据假设、异常标签需求、迁移模块、计算成本、指标兼容性、泄漏风险、工程难度和最小验证实验的信息增益。

结论允许：

```text
high / medium / low / reject / insufficient_information
```

### 10.4 Experiment Planner

必须包含：实验目标、候选 idea ID、baseline、method variant、dataset/categories、metrics、对照组、实验组、资源预算、预期效果、风险、成功标准和停止条件。

### 10.5 Code Patch Planner 与修改时间估计

估计依据：文件数量、代码规模、新增模块、依赖、配置、测试、仓库熟悉度、历史相似任务耗时和未确认接口数量。

输出必须是区间和置信度，不是确定承诺：

```json
{
  "estimated_minutes": {"low": 15, "high": 35},
  "confidence": "medium",
  "risk_factors": ["feature shape 未确认", "现有测试覆盖不足"]
}
```

### 10.6 Environment Profiler 与运行时间估计

批准执行后检测：GPU 型号/数量/显存、CUDA/Python/PyTorch、CPU/RAM、数据集大小、分辨率、batch size、epoch、类别数量、缓存特征和 checkpoint。

输出时间区间、置信度和 OOM 风险。实验运行后根据真实吞吐动态修正剩余时间。该功能必须使用历史实验数据校准，不能只让 LLM 猜测。

### 10.7 Runner / Sandbox

只应用已批准 patch，只执行白名单命令，不覆盖旧实验结果，保存 command、config、stdout、stderr、metrics 和失败栈。

### 10.8 Metrics Analyzer 与 Validity Supervisor

除指标对比外，还要检查：dataset split、test label/mask、evaluation script、异常样本是否混入训练、是否只挑有利类别、单 seed 偶然提升、指标和后处理口径是否变化，以及是否需要 ablation。

### 10.9 Multi-Agent Reflection Team

实验后并行四个受控角色：

| 角色 | 职责 |
|---|---|
| Debug Agent | 代码、依赖、shape、显存和错误栈 |
| Method Critic | 方法假设是否适合当前异常检测任务 |
| Experiment Designer | 下一轮实验、对照组和 ablation |
| Validity Judge | 当前证据是否足以支持科研结论 |

每个 Agent 必须基于已落盘 artifact，输出假设、证据、行动、成本、科研价值和置信度。Supervisor 去重、排除违反协议的方案，并按成功概率、成本和信息增益排序。

第一版最多保留 2–3 条候选分支，避免 token、GPU 和调试成本失控。

### 10.10 Report Generator 与可视化

以下状态都必须生成报告：

```text
success / failed / partial_success / stopped / rejected
```

报告固定包含：目标、论文摘要、候选 ideas、用户选择、每轮计划、代码修改、环境与耗时、指标与图表、baseline 对比、成功依据或失败根因、有效性检查、多 Agent 分歧、最终结论、限制、下一步建议和 artifact 索引。

建议可视化：候选 idea 比较、指标对比、多轮趋势、实验时间线、失败类型、资源与 token 消耗、候选假设处理结果。

---

## 11. PipelineController 的长期定位

当前 MVP 可以从最小控制器开始：

```text
experiment_planning → patch_planning
```

长期目标是：

> **基于 artifact、events、审批和停止条件驱动的多轮科研闭环控制器。**

职责边界：

```text
PipelineController：run 生命周期、stage 顺序、idea 分支、循环、审批、失败和停止条件
IdeaGenerationBackend：产生结构化候选方案
Harness：执行具体 stage，生成 artifact，返回 StageResult
ArtifactStore：保存事实和结果
EventStore：记录全过程
Workflow Runtime：后期提供可靠恢复和队列能力
```

MVP 继续采用清晰的 Python controller。只有在复杂恢复或持久化执行成为真实瓶颈时，再评估 LangGraph / Temporal。

---

## 12. 模型路由与成本控制

```text
light_model：摘要、抽取、日志归纳、初步判断
strong_model：实验设计、代码 patch、复杂 debug、最终结论
critic_model：多 Agent 质疑、有效性检查
embedding_model：文献和历史实验检索
local_model：离线或隐私任务
```

稳定前缀放系统角色、工具 schema、项目协议、安全边界和输出 schema；动态后缀放当前论文、用户输入、日志、diff 和错误栈。

记录每次调用的 model、latency、tokens、cache hit/miss、estimated cost、stage、agent_role 和 run_id。

---

## 13. 安全与人工确认

允许：读取指定论文和 repo、修改指定实验工作区、生成和应用已批准 patch、运行白名单 benchmark、读取日志、生成报告。

禁止：删除 dataset/项目、访问敏感路径、未授权上传、安装未知包、危险 shell、覆盖 baseline、修改固定 evaluation script、未经确认启动大规模训练。

至少保留：

```text
1. 任务目标确认
2. 候选 idea 选择或并行确认
3. 实验方案确认
4. patch 和代码修改时间确认
5. 实验命令、资源和预计运行时间确认
6. 下一轮实验分支确认
7. 最终停止或继续确认
```

---

## 14. 当前实现状态与近期路线

已完成（地基阶段）：

```text
run_id 安全校验（core/run_id.py）
ArtifactStore（JSON + YAML）
EventStore / events.jsonl
StageResult + StageStatus
AgentHarness（ABC）
SimplePipelineHarness + DeepAgentsHarness
artifact_written / artifact_read
stage_started / stage_completed / stage_failed
PipelineController
PipelineResult + PipelineStatus
Pipeline failure handling（stage_failed 事件 + failed 结果）
deterministic smoke CLI（uv run autoad smoke --run-id run_demo）
Input Intake / Source Manifest（input_task.yaml + source_manifest.json）
Paper Reader + Repository Reader 协议（PaperSummary, RepositorySummary, EvidenceReference）
Evidence-based Intent Clarifier（ClarifiedTask, KnownFact, MissingInformation, ClarificationQuestion）
Idea protocol + Idea Source Router（IdeaMode, IdeaCandidate, IdeaContext, IdeaRouteDecision）
DirectIdeaBackend + IdeaGenerator（deterministic direct_user_idea flow）
IdeaContext 自校验 + EstimatedIdeaCost（含 unknown）
本地 verify gate（12 项检查 + pytest）+ GitHub Actions = 204 passed
```

> **地基阶段结束。下一步进入真实纵向能力：固定论文、固定 PatchCore 仓库、固定 MVTec AD 类别，不再继续搭抽象层。**

近期顺序：

```text
Step 3.1：真实 Paper Reader（MinerU 或 MarkItDown 单篇论文解析）
Step 3.2：真实 Repository Reader（本地 PatchCore 仓库加载与摘要）
Step 3.3：Transferability Judge（可迁移性判断）
Step 3.4：动态 Experiment + Patch Planning（替代 SimplePipelineHarness 占位输出）
之后：Approval / Runner / Metrics / Validity / Reflection / Report
```

---

## 15. 实现优先级

### P0：地基与最小闭环

```text
AutoAD Core 和 PipelineController
输入接收和 artifact 工作区
Paper Reader + Repository Reader
基于材料的 Intent Clarifier
IdeaCandidate schema 和框架无关接口
DirectIdeaBackend
Transferability Judge
Experiment Planner
Patch Plan + 人工确认
固定 baseline 的受控 smoke test
日志、指标和有效性分析
成功或失败均生成报告
```

### P1：智能能力和实用性增强

```text
AutoGen Idea Deliberation spike
CrewAI 对照 spike
DeepAgents Ideation 对照
Pydantic AI 结构化 Agent 评估
代码修改时间估计
Environment Profiler
训练和剩余时间估计
多模型路由与缓存统计
多 Agent 反思团队
2–3 条候选实验分支
报告可视化
历史实验检索
```

### P2：可靠运行时和领域工具增强

```text
LangGraph / Temporal
OpenHands / SWE-agent 代码执行增强
LlamaIndex / RAG
MLflow / OpenTelemetry
多论文、多数据集、多 baseline
复杂自动 ablation
多 GPU 并行分支
跨 run 科研记忆与经验蒸馏
```

原则：

> **先定义稳定协议和事实层，再比较 Agent 框架；先跑通可控闭环，再增加框架和 Agent 数量。**

---

## 16. 对外汇报口径

> AutoAD-Researcher 面向异常检测科研中的“论文理解—方法迁移—代码修改—实验验证—结果反思”流程，构建人机协同的 Dry Lab 闭环。系统先解析用户提供的论文、代码仓库和实验上下文，再针对真正缺失的信息与研究者沟通。当用户没有明确 idea 时，系统通过多个专业 Agent 从方法、异常检测领域、模型架构、实验设计和科研有效性角度产生并质疑多个候选方案；当用户已有 idea 但实现方式不唯一时，系统生成不同插入位置和实现路径，供用户选择单线或多线并行。选定方案由 Deep Agents 等执行内核推进到规划和代码任务，由 AutoAD Core 统一管理 artifact、审批、实验执行和有效性。无论成功、失败还是停止，系统都会生成完整报告、可视化和证据链。

---

## 17. 一句话总结

> **AutoAD Core 管流程、事实、审批、安全和科研有效性；AutoGen/CrewAI 等负责候选思想碰撞；Deep Agents 负责把选定思想推进成长程任务；Pydantic AI 提供类型安全 Agent 参考；LangGraph/Temporal 后期提供可靠运行能力；领域工具按需增强具体功能。**

关联文档：

- [AutoAD-Researcher 后端框架选型比较](./backend_framework_comparison.md)
- [AutoAD-Researcher 参考资料汇总](./AutoAD_参考资料汇总.md)
