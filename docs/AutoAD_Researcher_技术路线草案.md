# AutoAD-Researcher 技术路线草案

> 用途：与导师讨论参赛方向、项目边界、MVP 和实现路线。  
> 当前版本：讨论稿 v3（2026-06-16）。  
> 本版统一了材料解析、意图澄清、时间估计、多 Agent 反思和多轮实验闭环的设计口径。赛题平台和测试环境尚未完全明确，本文不做确定性假设。

---

## 1. 项目定位

> **AutoAD-Researcher：面向异常检测的文献迁移与实验闭环智能体**

给定论文、方法想法、代码仓库和实验目标，系统先解析用户已经提供的材料，再针对真正缺失的信息与研究者沟通；随后判断方法是否适合迁移到异常检测，生成实验方案和代码修改计划，在人工确认后执行受控实验，并通过指标分析、多 Agent 反思和多轮迭代形成可追溯结论。

本项目：

- 不是通用编程 Agent；
- 不是全自动 AI 科学家；
- 不是复刻完整 AutoSOTA；
- 不把 Deep Agents、LangGraph 或其他框架当作项目本体；
- 是一个 Human-in-the-loop 的异常检测 Dry Lab 闭环系统。

核心原则：

> **AutoAD Core 是领域闭环控制层；Deep Agents 等框架只是可替换的 Harness Backend。**

`run_id`、schema、artifact、审批、事件、白名单执行、循环、停止条件和科研有效性必须由 AutoAD Core 控制。

---

## 2. 真实科研闭环

项目不是“运行一次实验后结束”的线性流水线，而是：

```text
论文 / 方法想法 / repo / dataset / baseline / 资源约束
→ 输入接收与材料汇总
→ 论文和代码仓库解析
→ 基于已有材料的意图澄清
→ 方法迁移可行性判断
→ 实验方案生成
→ 代码修改计划、patch 和工作量估计
→ 人工确认
→ 环境检测和实验运行时间估计
→ 受控实验执行
→ 日志、指标和科研有效性分析
→ 多 Agent 并行反思与候选分支生成
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
- 不让默认 shell / execute 绕过 AutoAD 白名单。

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
Transferability Judge
  - 判断任务设定兼容性、迁移位置、风险和验证价值
  │
  ▼
Experiment Planner
  - baseline、dataset、metric、对照组、预算、成功与停止条件
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

例如，论文已经明确仅使用正常样本训练，系统不应再问“是否包含异常标签”，而应询问：“你希望保持无监督设定，还是允许少量异常标签做扩展实验？”

因此 Intent Clarifier 是 **基于证据的澄清器**，不是固定问卷。

---

## 5. 工程分层

### 5.1 AutoAD Core：领域控制层

负责：

```text
run_id / PipelineController
Pydantic / JSON Schema
ArtifactStore / EventStore
StageResult / PipelineResult
approval checkpoints
循环、分支和停止条件
sandbox / command whitelist
Scientific Validity Supervisor
final report 生成约束
```

### 5.2 Harness Backend：智能执行层

```text
AgentHarness
├── SimplePipelineHarness
│   └── deterministic smoke-test backend
│       仅验证 Core、schema、artifact、events 和 CI
│
├── DeepAgentsHarness
│   └── 调用 LLM / Deep Agents 完成复杂规划和分析
│
└── 后续可选 OpenHandsHarness / PydanticAIHarness 等
```

关键口径：

> SimplePipelineHarness 不是科研智能体，也不代表项目最终能力。

> DeepAgentsHarness 负责“想和写”；AutoAD Core 负责“管和验”。

### 5.3 Tools / Services

```text
Paper Parser：MinerU / MarkItDown
Repository Reader
Model Gateway / Router
Code Patch Tool
Environment Profiler
Runtime Estimator
Runner / Sandbox
Metrics Parser
Visualization
Report Generator
```

### 5.4 运行平台

```text
本地开发环境
Docker / conda
GitHub / CI/CD
CLI + Gradio / Streamlit
后续 FastAPI / Temporal
```

---

## 6. Artifact-first 与唯一事实源

对话历史、Agent memory 和虚拟文件系统都不能作为最终事实源。

```text
runs/{run_id}/
  input_task.yaml
  source_manifest.json
  paper_summary.json
  repo_summary.json
  clarified_task.json
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
- 成功和失败都保留证据。

---

## 7. 关键模块设计

### 7.1 Paper Reader 与 Repository Reader

Paper Reader 输出：核心方法、模型组件、数据假设、标签需求、训练目标、数据集、指标、代码可用性、潜在迁移点和未解决问题。

Repository Reader 输出：repo 结构、训练/推理/评价入口、baseline 配置、可修改与禁止修改文件、测试命令，以及固定 evaluation script 的版本或指纹。

### 7.2 Intent Clarifier

输入：用户原始输入、`paper_summary.json`、`repo_summary.json` 和已知环境信息。

输出：已知事实、缺失信息、关键问题、`clarified_task.json` 和用户确认状态。禁止重复询问材料中已明确的信息。

### 7.3 Transferability Judge

判断：数据假设、异常标签需求、迁移模块、计算成本、指标兼容性、泄漏风险、工程难度和最小验证实验的信息增益。

结论允许：

```text
high / medium / low / reject / insufficient_information
```

### 7.4 Experiment Planner

必须包含：实验目标、baseline、method variant、dataset/categories、metrics、对照组、实验组、资源预算、预期效果、风险、成功标准和停止条件。

### 7.5 Code Patch Planner 与修改时间估计

代码修改时间估计依据：

- 需要阅读和修改的文件数量；
- 预计代码改动规模；
- 是否新增模块、依赖、配置和测试；
- 仓库熟悉程度；
- 历史相似任务耗时；
- 未确认接口和 shape 数量。

输出必须是区间和置信度，不是确定承诺：

```json
{
  "estimated_minutes": {"low": 15, "high": 35},
  "confidence": "medium",
  "risk_factors": ["feature shape 未确认", "现有测试覆盖不足"]
}
```

### 7.6 Environment Profiler 与运行时间估计

批准执行后检测：GPU 型号/数量/显存、CUDA/Python/PyTorch、CPU/RAM、数据集大小、分辨率、batch size、epoch、类别数量、缓存特征和 checkpoint。

输出时间区间、置信度和 OOM 风险。实验运行后根据真实吞吐动态修正剩余时间。该功能必须使用历史实验数据校准，不能只让 LLM 猜测。

### 7.7 Runner / Sandbox

只应用已批准 patch，只执行白名单命令，不覆盖旧实验结果，保存 command、config、stdout、stderr、metrics 和失败栈。

### 7.8 Metrics Analyzer 与 Validity Supervisor

除指标对比外，还要检查：dataset split、test label/mask、evaluation script、异常样本是否混入训练、是否只挑有利类别、单 seed 偶然提升、指标和后处理口径是否变化，以及是否需要 ablation。

### 7.9 Multi-Agent Reflection Team

实验后并行四个受控角色：

| 角色 | 职责 |
|---|---|
| Debug Agent | 代码、依赖、shape、显存和错误栈 |
| Method Critic | 方法假设是否适合当前异常检测任务 |
| Experiment Designer | 下一轮实验、对照组和 ablation |
| Validity Judge | 当前证据是否足以支持科研结论 |

每个 Agent 必须基于已落盘 artifact，输出假设、证据、行动、成本、科研价值和置信度。Supervisor 去重、排除违反协议的方案，并按成功概率、成本和信息增益排序。

第一版最多保留 2–3 条候选分支，避免 token、GPU 和调试成本失控。

### 7.10 Report Generator 与可视化

以下状态都必须生成报告：

```text
success / failed / partial_success / stopped / rejected
```

报告固定包含：目标、论文摘要、迁移判断、每轮计划、代码修改、环境与耗时、指标与图表、baseline 对比、成功依据或失败根因、有效性检查、多 Agent 意见、最终结论、限制、下一步建议和 artifact 索引。

建议可视化：指标对比、多轮趋势、实验时间线、失败类型、资源与 token 消耗、候选假设处理结果。

失败报告同样有科研价值：它应清楚证明某个方法为什么不适合当前任务。

---

## 8. PipelineController 的长期定位

当前 MVP 可以从最小控制器开始：

```text
experiment_planning → patch_planning
```

长期目标是：

> **基于 artifact、events、审批和停止条件驱动的多轮科研闭环控制器。**

职责边界：

```text
PipelineController：run 生命周期、stage 顺序、循环、分支、审批、失败和停止条件
Harness：执行具体 stage，生成 artifact，返回 StageResult
ArtifactStore：保存事实和结果
EventStore：记录全过程
```

MVP 继续采用清晰的 Python controller。只有在复杂恢复或持久化执行成为真实瓶颈时，再评估 LangGraph / Temporal。

---

## 9. 模型路由与成本控制

```text
light_model：摘要、抽取、日志归纳、初步判断
strong_model：实验设计、代码 patch、复杂 debug、最终结论
embedding_model：文献和历史实验检索
local_model：离线或隐私任务
```

稳定前缀放系统角色、工具 schema、项目协议、安全边界和输出 schema；动态后缀放当前论文、用户输入、日志、diff 和错误栈。

记录每次调用的 model、latency、tokens、cache hit/miss、estimated cost、stage 和 run_id。

---

## 10. 安全与人工确认

允许：读取指定论文和 repo、修改指定实验工作区、生成和应用已批准 patch、运行白名单 benchmark、读取日志、生成报告。

禁止：删除 dataset/项目、访问敏感路径、未授权上传、安装未知包、危险 shell、覆盖 baseline、修改固定 evaluation script、未经确认启动大规模训练。

至少保留：

```text
1. 任务目标确认
2. 实验方案确认
3. patch 和代码修改时间确认
4. 实验命令、资源和预计运行时间确认
5. 下一轮实验分支确认
6. 最终停止或继续确认
```

---

## 11. 当前实现状态与近期路线

已完成：

```text
run_id 安全校验
ArtifactStore
EventStore / events.jsonl
StageResult
AgentHarness
SimplePipelineHarness
DeepAgentsHarness
artifact_written / artifact_read
stage_started / stage_completed / stage_failed
本地 verify gate 和 GitHub Actions
```

当前 SimplePipelineHarness 仅是 deterministic smoke-test backend。

近期顺序：

```text
Step 2.8：Minimal PipelineController
Step 2.9：Pipeline failure handling
随后：Input Intake / Paper Reader contract / Intent Clarifier
再后：Approval / Runner / Metrics / Validity / Reflection / Report
```

---

## 12. 实现优先级

### P0

```text
AutoAD Core 和 PipelineController
输入接收和 artifact 工作区
Paper Reader + Repository Reader
基于材料的 Intent Clarifier
Transferability Judge
Experiment Planner
Patch Plan + 人工确认
固定 baseline 的受控 smoke test
日志、指标和有效性分析
成功或失败均生成报告
```

### P1

```text
代码修改时间估计
Environment Profiler
训练和剩余时间估计
多模型路由与缓存统计
多 Agent 反思团队
2–3 条候选实验分支
报告可视化
历史实验检索
```

### P2

```text
多论文、多数据集、多 baseline
复杂自动 ablation
多 GPU 并行分支
Temporal / LangGraph
MLflow / OpenTelemetry
跨 run 科研记忆与经验蒸馏
```

原则：

> **先跑通可控闭环，再增加 Agent 数量和框架复杂度。**

---

## 13. 对外汇报口径

> AutoAD-Researcher 面向异常检测科研中的“论文理解—方法迁移—代码修改—实验验证—结果反思”流程，构建人机协同的 Dry Lab 闭环。系统先解析用户提供的论文、代码仓库和实验上下文，再针对真正缺失的信息与研究者沟通；在实验规划和代码修改后给出工作量与运行时间估计，经人工确认后执行受控实验；结果由多个专业 Agent 从代码、方法、实验设计和科研有效性角度并行分析，并在预算和停止条件约束下推动下一轮实验。无论成功、失败还是停止，系统都会生成完整报告、可视化和证据链。

---

## 14. 一句话总结

> **AutoAD Core 管流程、事实、审批、安全和科研有效性；Harness Backend 提供智能执行；实验通过多轮反馈和受控多 Agent 反思逐步推进，最终无论成败都形成可追溯结论。**

关联文档：

- [AutoAD-Researcher 后端框架选型比较](./backend_framework_comparison.md)
- [AutoAD-Researcher 参考资料汇总](./AutoAD_参考资料汇总.md)
