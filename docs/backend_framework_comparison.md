# AutoAD-Researcher 后端框架选型比较

> **文档状态：** 讨论稿 v1 — 在技术路线草案 v2 的第二层架构选型基础上整理。  
> **关联文档：** [AutoAD_Researcher_技术路线草案.md](./AutoAD_Researcher_技术路线草案.md)  
> **用途：** 为 AutoAD Core 选择合适的 Agent 执行内核（harness backend）、Pipeline 编排运行时、以及各子系统的技术选型提供依据。

---

## 0. 选型前提

### 0.1 AutoAD Core 的定位

引用技术路线草案中的第一层开发原则：

> **AutoAD Core 是领域闭环控制层，Deep Agents 是可选执行内核 / harness backend。**  
> `run_id` 生命周期、结构化 schema、实验资产、审批、日志、白名单执行和科研有效性监督必须由 AutoAD Core 控制。

这意味着后端框架选型解决的是 **第二层问题**：谁来做 Agent 执行、长任务编排、结构化输出、人机协同 — 而不是谁来替代 AutoAD Core 的领域逻辑。

### 0.2 分层选型框架

```
┌──────────────────────────────────────────────────┐
│  AutoAD Core（自研，不可替换）                      │
│  - 领域闭环控制 - Schema/Artifact - 审批/安全       │
├──────────────────────────────────────────────────┤
│  Harness Backend（候选替换）                        │
│  - Agent 生命周期 - 工具调用 - 人机协同 - 文件系统   │
├──────────────────────────────────────────────────┤
│  Pipeline Runtime（候选替换）                       │
│  - 状态机 - DAG/Workflow - 持久化 - 可恢复          │
├──────────────────────────────────────────────────┤
│  Subsystems（按需组合）                             │
│  - RAG/知识检索 - 代码沙盒 - Eval - 多模型路由      │
└──────────────────────────────────────────────────┘
```

### 0.3 优先级定义

| 优先级 | 含义 |
|--------|------|
| **必须保留** | 不可替换，作为整个系统的基础 |
| **P0（当前主候选）** | 最可能成为 harness backend 的框架 |
| **P1（重点比较）** | 需要与 P0 深入对比后决定 |
| **P2（可比较）** | 有价值但非首选，或适用范围较窄 |
| **P3（后期再评估）** | 当前阶段不需要，后期某个子系统可能用到 |
| **低优先级** | 与我们的需求匹配度低，不建议深入 |

---

## 1. 综合比较矩阵

### 1.1 控制层（必须保留）

| 框架 | 类型 | 对 AutoAD 的潜在用途 | 优势 | 风险 / 劣势 | 建议 |
|------|------|---------------------|------|------------|------|
| **自研 AutoAD Core + SimplePipeline** | 自研控制层 + 测试后端 | 地基、CI、artifact、schema、events | 最可控、最适合学习、最容易 debug | 没有 AI 能力 | **必须保留** |

> AutoAD Core 不与其他框架竞争 — 它是所有其他框架的"上层建筑"。其他框架解决的是 Core 下面的执行问题。

### 1.2 Harness Backend（Agent 执行内核）

| 框架 | 类型 | 对 AutoAD 的潜在用途 | 优势 | 风险 / 劣势 | 建议 |
|------|------|---------------------|------|------------|------|
| **Deep Agents** | Agent harness | 长程论文/代码/日志任务 | planning、filesystem、subagents、context management、permission、人类审批能力较贴近需求 | 抽象较重，输出稳定性需验证 | **P0 当前主候选** |
| **Pydantic AI** | Python agent framework | 类型安全 agent、结构化输出、eval、轻量 agent stage | 与 Pydantic schema 天然契合，模型无关，强调类型安全和 eval | 生态和 Deep Agents 的长程文件系统能力不同，需要验证 | **P1 重点比较** |
| **AutoGen** | 多 Agent 框架 | 研究多 Agent 协作、对话式 planner/fixer | 有 AgentChat、Core、Studio，适合单/多 Agent 应用和事件驱动系统 | 对我们这种 artifact-first 科研系统可能偏重 | **P2 可比较** |
| **CrewAI** | 多 Agent / crews / flows | 多角色科研团队：PaperReader、Planner、Fixer、Reporter | Crews/Flows 概念清楚，支持 guardrails、memory、knowledge、observability | 容易变成角色扮演式多 Agent，科研有效性仍要自己管 | **P2 可比较，不宜先接入** |

### 1.3 Pipeline Runtime（流程编排运行时）

| 框架 | 类型 | 对 AutoAD 的潜在用途 | 优势 | 风险 / 劣势 | 建议 |
|------|------|---------------------|------|------------|------|
| **LangGraph** | 低层 agent orchestration runtime | 显式状态机、PipelineController、可恢复 workflow | 官方定位是 durable execution、streaming、human-in-the-loop、persistence，控制力强 | 学习曲线高，容易过早工程化 | **P1 重点比较** |
| **Temporal** | 长任务 workflow runtime | 长实验、失败恢复、任务队列 | 强可靠执行，崩溃后可恢复 | 不是 Agent 框架，接入较重 | **P3 后期长实验再考虑** |

### 1.4 Domain-Specific（特定子系统）

| 框架 | 类型 | 对 AutoAD 的潜在用途 | 优势 | 风险 / 劣势 | 建议 |
|------|------|---------------------|------|------------|------|
| **OpenHands SDK** | 软件工程 Agent SDK | 代码仓库修改、测试、沙盒执行 | 面向软件开发 agent，强调 sandbox、生命周期、模型路由、安全分析 | 目标偏通用 SWE，不天然懂异常检测科研协议 | **P2 后续代码修改阶段重点看** |
| **SWE-agent** | 软件工程 agent / ACI | patch、repo 导航、测试执行 | 在 repo 导航、编辑、测试方面有明确 Agent-Computer Interface 设计 | 偏 SWE-bench 修 bug，不是科研闭环 | **P2 后续 patch 阶段参考** |
| **LlamaIndex Agents / Workflows** | RAG / agent / workflow | 论文资料检索、知识库、paper memory | 文档/知识检索强，适合论文解析和资料库 | 不适合作为整个科研闭环控制层 | **可作为 PaperReader/RAG 子系统** |
| **Google ADK** | Agent Development Kit | 生产级 agents、graph workflows、multi-agent、eval、deployment | 支持多语言，官方强调 graph workflows、协作 agents、eval、deployment | 生态偏 Google/Gemini，接入成本待评估 | **P2 可比较** |
| **Semantic Kernel** | AI middleware / agent toolkit | 企业插件、函数调用、模型接入 | 适合把已有 API/函数暴露给模型，支持 C#/Python/Java | 对 Python 科研原型可能不如 Pydantic/Deep Agents 顺手 | **低优先级比较** |

---

## 2. 关键候选深度分析

### 2.1 Deep Agents — P0 当前主候选

**官方定位：** 建立在 LangGraph 之上的 Agent harness。提供 planning、filesystem、subagents、context management、permission 等开箱即用的 Agent 能力。

**与 AutoAD 的匹配点：**

| AutoAD 需求 | Deep Agents 能力 | 匹配度 |
|------------|-----------------|--------|
| 长程论文→代码→日志任务 | Subagents + planning | ★★★★ |
| 文件系统操作（代码 patch、日志读写） | Filesystem tools | ★★★★★ |
| 5 个确认 gate 的人工审批 | Permission / human-in-the-loop | ★★★★ |
| 多模块串联 | Agent composition | ★★★ |
| 结构化输出 | 需结合 Pydantic | ★★★ |

**待验证风险：**
- 抽象层较重，debug 时可能穿透多层
- 输出稳定性：长程任务中 Agent 是否容易偏离目标
- 与 AutoAD Core 的边界划分需要明确约定

### 2.2 LangGraph — P1 重点比较

**官方定位：** 低层 Agent orchestration runtime。强调 durable execution、streaming、human-in-the-loop、persistence。

**关键洞察：** LangGraph 和 Deep Agents 不是竞争关系 — Deep Agents 是建立在 LangGraph 之上的。两者的取舍本质上是：

> **直接基于 LangGraph 自建编排层** vs **使用 Deep Agents 提供的更高层抽象**

| 维度 | LangGraph 直接使用 | Deep Agents（基于 LangGraph） |
|------|-------------------|------------------------------|
| 控制力 | 极高，可精确控制每个状态转换 | 中等，受限于 harness 抽象 |
| 开发速度 | 慢，需要自己实现 Agent 循环 | 快，开箱即用 |
| 学习曲线 | 高，需要理解 StateGraph、Checkpoint 等概念 | 中等 |
| 适合场景 | 对控制流有精确要求的科研流程 | 需要快速验证 Agent 能力的原型阶段 |
| 过早工程化风险 | 高 — 容易在 MVP 前花太多时间在框架上 | 低 — 可以快速出原型 |

**建议：** MVP 阶段优先用 Deep Agents 快速验证闭环可行性；如果后续发现 Deep Agents 的控制力不够，可以下沉到 LangGraph 自建编排。

### 2.3 Pydantic AI — P1 重点比较

**官方定位：** Python agent framework，强调 production-grade GenAI workflows、模型无关、类型安全、eval、human-in-the-loop 和 durable execution。

**突出优势（对 AutoAD 特别重要）：**

- **类型安全：** 与 Pydantic v2 天然契合。AutoAD Core 的核心设计原则之一就是"结构化 schema"，Pydantic AI 在这一维度上是最强候选。
- **模型无关：** 不绑定特定模型提供商，适合我们的多模型 gateway 设计。
- **Eval 支持：** 内置 evaluation 能力，对科研可复现性很重要。

**与 Deep Agents 的互补/竞争关系：**

| 维度 | Pydantic AI | Deep Agents |
|------|------------|-------------|
| 类型安全 / Schema | ★★★★★ 核心卖点 | ★★★ 需额外集成 |
| 长程 Agent 任务 | ★★★ 有 agent 但偏轻量 | ★★★★★ 核心卖点 |
| 文件系统操作 | ★★ 需自行扩展 | ★★★★★ 内置 |
| Eval | ★★★★ 内置 | ★★ 需自行搭建 |
| 模型无关性 | ★★★★★ | ★★★ |

**建议：** Pydantic AI 和 Deep Agents 可能是 **互补关系** 而非替代关系。可以考虑：
- AutoAD Core 的 Schema / 结构化输出层用 Pydantic AI
- Agent 执行 / 长程任务用 Deep Agents
- 两者通过 AutoAD Core 的 gateway 层桥接

---

## 3. 推荐组合方案

### 方案 A："Deep Agents 主线"（推荐 MVP 先试）

```
AutoAD Core（自研）
├── Agent Harness: Deep Agents
│   └── 底层: LangGraph（Deep Agents 自带，不直接使用）
├── Schema / 结构化输出: Pydantic v2（自研集成）
├── Paper Reader / RAG: LlamaIndex
├── 代码修改 / Patch: SWE-agent 或 OpenHands 参考设计
├── 实验执行: SimplePipeline → 后期考虑 Temporal
└── 模型路由: 自研 gateway（模型无关）
```

### 方案 B："LangGraph 自建"（后期备选）

```
AutoAD Core（自研）
├── Pipeline Runtime: LangGraph（自建 StateGraph）
├── Agent 循环: 自建（参考 Deep Agents / SWE-agent 设计）
├── Schema: Pydantic AI + Pydantic v2
├── Paper Reader: LlamaIndex
└── ...
```

**切换时机判断：** 当 Deep Agents 在以下任何维度出现不可接受的限制时，切换到方案 B：
- 无法精确控制 Agent 的状态转换
- 审批 gate 的实现与 Deep Agents 的 permission 模型冲突
- Debug 成本超过自建成本

### 方案 C："Pydantic AI 中心"（需要验证后再决定）

```
AutoAD Core（自研）
├── Agent Framework: Pydantic AI
├── Long-running tasks: Temporal 或 LangGraph
├── Schema: Pydantic AI 原生
└── ...
```

**前提条件：** 需要先验证 Pydantic AI 在长程文件系统任务上的能力是否满足 AutoAD 的需求。

---

## 4. 决策时间线

```
MVP 前（当前 → 1-2 周）
├── AutoAD Core + SimplePipeline 搭建 ← 必须保留
├── Deep Agents 原型验证 ← P0
│   └── 验证点：长程任务稳定性、审批 gate 适配
├── Pydantic AI 轻量评估 ← P1
│   └── 验证点：Schema 集成、与 Pydantic v2 的协同
└── LlamaIndex 作为 Paper Reader 子系统接入

MVP 中（2-4 周）
├── 根据 Deep Agents 验证结果决定是否下沉到 LangGraph
├── OpenHands / SWE-agent 参考设计引入 patch 阶段
└── Pydantic AI 若通过评估则集成到 schema 层

MVP 后（4 周+）
├── 长实验稳定性 → 评估 Temporal
├── Eval 体系 → Pydantic AI eval 或自建
└── 多 Agent 协作 → 重新评估 CrewAI / AutoGen
```

---

## 5. 各框架官方自我定位摘要

以下信息提取自各框架官方文档，用于校准我们的比较判断：

| 框架 | 官方自我定位 | 关键能力声明 |
|------|------------|------------|
| **LangGraph** | 低层 orchestration runtime | durable execution、streaming、human-in-the-loop、persistence |
| **Deep Agents** | 建立在 LangGraph 之上的 agent harness | planning、filesystem、subagents、context management |
| **Pydantic AI** | Python agent framework | production-grade GenAI workflows、模型无关、类型安全、eval、human-in-the-loop、durable execution |
| **CrewAI** | 多 Agent 平台 | agents、crews、flows、guardrails、memory、knowledge、observability、Flows 的状态管理/持久化/恢复 |
| **AutoGen** | 构建 AI agents and applications 的框架 | AgentChat、Core、Extensions、Studio；Core 是事件驱动的可扩展多 Agent 系统 |
| **OpenHands** | 面向软件开发 agent 的平台 | 写代码、命令行、浏览器、沙盒、多 Agent、benchmark |
| **SWE-agent** | 软件工程 agent | Agent-Computer Interface (ACI) 对代码编辑、repo 导航和测试执行 |
| **Google ADK** | 构建/调试/部署 production agents 的开源框架 | graph workflows、multi-agent workflows、runtime、evaluation、安全 |
| **Temporal** | 可靠工作流平台（非 Agent 框架） | 崩溃/网络故障/基础设施故障后能从中断处恢复 |

---

## 6. 补充说明

### 6.1 关于"过早工程化"

LangGraph 的学习曲线和工程化程度是实际风险 — 在 MVP 阶段花两周搭建 LangGraph StateGraph 而核心闭环还没跑通，是本末倒置。当前策略是 **先用 Deep Agents 跑通闭环，控制层留在 AutoAD Core，需要时再下沉**。

### 6.2 关于"角色扮演式多 Agent"

CrewAI 和 AutoGen 的文档中大量使用角色（PaperReader、Planner、Fixer、Reporter）来描述 Agent。这种抽象在 demo 中很直观，但在科研场景中：
- 角色之间的信息传递可能丢失关键细节
- 角色的"主观判断"难以审计
- 容易把 prompt 工程问题误认为是 Agent 架构问题

AutoAD 的选择是：**模块是确定性的 pipeline 步骤，Agent 只在需要 LLM 判断时介入，不做全角色 Agent 化。**

### 6.3 artifact-first 原则

AutoAD 是 artifact-first 系统（论文→parse result→transfer judgment→experiment plan→patch→run→report），不是对话-first 系统。这个根本差异决定了：
- 框架的"对话管理"能力强 ≠ 适合我们
- 框架的"文件/状态持久化"能力更关键
- 框架的"结构化输出"能力直接影响数据流质量
