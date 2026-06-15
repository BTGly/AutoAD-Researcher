# AutoAD-Researcher 技术路线草案

> 用途：用于和导师讨论是否参加 AI for Science 相关比赛，以及确定项目边界、MVP、技术路线和队伍分工。  
> 当前版本：讨论稿 v2。本文已吸收“AutoAD Core + DeepAgentsHarness”的第一层开发路线。部分赛题平台、提交方式、测试环境尚不明确，本文不做确定性假设。

---

## 1. 项目定位

项目暂定名称：

> **AutoAD-Researcher：面向异常检测的文献迁移与实验闭环智能体**

一句话描述：

> 给定一篇论文、一个方法想法或一个实验目标，系统能够判断该方法是否适合迁移到异常检测任务，生成实验方案，辅助修改代码，运行小规模实验，读取日志和指标，并输出结果分析和下一轮建议。

核心定位：

- 不是通用编程 Agent；
- 不是全自动 AI 科学家；
- 不是复刻 AutoSOTA；
- 不是把 Deep Agents、LangGraph、CrewAI 或任一 Agent 框架当作项目本体；
- 而是一个面向异常检测科研流程的 **Dry Lab 闭环系统**。

当前第一层开发原则：

> **AutoAD Core 是领域闭环控制层，Deep Agents 是可选执行内核 / harness backend。**  
> 即：可以在 Deep Agents 基础上做针对性修改和扩展，但 `run_id` 生命周期、结构化 schema、实验资产、审批、日志、白名单执行和科研有效性监督必须由 AutoAD Core 控制。

核心闭环：

```text
论文 / 方法想法 / 实验目标
→ 意图澄清
→ 论文理解
→ 方法可迁移性判断
→ 实验方案生成
→ 代码修改计划 / patch
→ 人工确认
→ 实验运行
→ 日志与指标读取
→ baseline 对比
→ 失败原因分析
→ 下一轮实验建议
→ Markdown 实验报告
```

---

## 2. 为什么这个方向可以参赛

### 2.1 科研痛点明确

异常检测研究中存在几个高频问题：

1. 新论文太多，研究者很难快速判断哪些方法值得跟进；
2. 很多跨领域方法可能有迁移价值，但迁移位置并不明确；
3. 方法能否提升异常检测效果，最终必须靠实验验证；
4. 实验过程涉及代码修改、参数配置、日志分析、指标对比，重复劳动多；
5. 失败实验往往也有价值，但需要系统化记录和反思。

因此项目的实际目标不是“让 AI 替代研究者”，而是：

> 帮助研究者把一个论文想法更快推进到可验证实验，并把实验过程保存成可追溯记录。

### 2.2 和 Dry Lab / 干闭环赛道匹配

该项目更适合选择：

> **B 类：AI4S 干闭环 / 计算与推演闭环方向**

原因：

| 赛道 | 适配度 | 判断 |
|---|---:|---|
| A：基础设施与工具 | 中 | 如果只做文献检索、综述、科研助手，会偏工具类 |
| B：干闭环 / 计算闭环 | 高 | 项目核心是规划实验、改代码、跑结果、分析结论 |
| C：湿闭环 | 低 | 当前没有物理实验设备、机器人或实验室仪器控制场景 |

参赛时应强调：

> 本项目不是单纯文献问答，而是把论文理解、实验规划、代码改写、实验执行和结果反思串成可控闭环。

---

## 3. 项目边界

### 3.1 不做什么

第一阶段不做以下内容：

- 不做全领域 AI 科学家；
- 不做自动发现 SOTA；
- 不支持所有异常检测子方向；
- 不同时支持大量论文和大量代码仓库；
- 不让 Agent 不经确认直接改代码和跑训练；
- 不依赖未经授权的泄露源码；
- 不把某个具体大模型 API 或 Agent 框架写死；
- 不把 Deep Agents 的默认执行能力直接暴露给科研实验流程；
- 不让 Agent 默认 shell / execute 工具绕过 AutoAD 的白名单和审批。

### 3.2 第一阶段只做什么

建议第一阶段收缩为：

```text
方向：视觉异常检测 / 工业缺陷检测
数据集：MVTec AD 或老师已有可公开/可演示数据集
baseline：PatchCore / PaDiM / FastFlow / Anomalib 中已有方法 / 课题组已有代码
任务：把一篇论文中的一个模块迁移到一个固定 baseline
目标：跑通一个最小实验闭环
输出：实验计划、patch、运行日志、指标表格、分析报告
```

---

## 4. 最小可行产品：MVP

### 4.1 MVP 输入

MVP 可以支持以下输入之一：

```text
1. 一篇论文 PDF
2. 一段论文摘要
3. 一个 arXiv 链接
4. 一个方法想法
5. 一个 GitHub 仓库链接
```

初版建议优先支持：

```text
论文摘要 / 论文 PDF + 固定异常检测 baseline
```

### 4.2 MVP 输出

系统至少输出：

1. 论文核心方法总结；
2. 是否适合迁移到异常检测；
3. 可迁移模块；
4. 不适合迁移的部分；
5. 推荐 baseline；
6. 推荐数据集和评价指标；
7. 最小实验方案；
8. 代码修改计划或 patch；
9. 运行命令；
10. 实验日志；
11. baseline 对比结果；
12. 失败原因或下一轮建议；
13. Markdown 实验报告。

### 4.3 MVP 成功标准

最低成功标准：

```text
输入一篇论文或方法想法
→ 系统生成结构化迁移判断
→ 系统生成最小实验方案
→ 系统生成代码修改计划或 patch
→ 人工确认
→ 跑一个固定 benchmark 或 smoke test
→ 系统读取指标
→ 输出实验报告
```

如果时间紧张，可以先实现：

```text
真实论文解析 + 真实实验规划 + 真实日志读取 + 半自动 patch
```

代码修改部分不必一开始完全自动化。

---

## 5. 系统总体架构

本项目第一层不再理解为“简单 Python pipeline 和 Deep Agents 二选一”，而是拆成：

```text
AutoAD Core：领域闭环控制层
  ↓
Harness Backend：执行内核，可选 SimplePipelineHarness / DeepAgentsHarness
  ↓
Tools / Services：论文解析、代码仓库读取、实验运行、日志分析、报告生成
```

也就是说：

```text
AutoAD Core 负责“科研闭环正确性”
Deep Agents 负责“长程 Agent 执行能力”
```

### 5.1 三层工程架构

```text
第一层：AutoAD Core
  - run_id 生命周期
  - Pydantic / JSON Schema
  - runs/{run_id}/ artifact workspace
  - events.jsonl / llm_calls.jsonl
  - approval checkpoints
  - Scientific Validity Supervisor
  - sandbox / command whitelist
  - final_report.md

第二层：Harness Backend
  - SimplePipelineHarness：稳定、可测试、可离线、适合 MVP smoke test
  - DeepAgentsHarness：基于 Deep Agents 的长程任务执行内核
  - 后续可选：OpenHandsHarness / CodeBuddyHarness / ClaudeAgentSDKHarness

第三层：运行与协作平台
  - 本地开发环境
  - Docker / conda
  - CNB / GitHub / CI/CD
  - Gradio / Streamlit / CLI
```

### 5.2 为什么引入 Deep Agents

Deep Agents 代表 2026 年开源 long-running agent harness 的趋势，适合处理复杂、多步骤、需要计划、文件系统、上下文管理和子任务分发的任务。它对本项目有直接价值：

```text
- planning：把科研任务拆成可执行步骤
- virtual filesystem：承载长程任务中的中间文件
- subagents：隔离论文理解、代码分析、日志分析等上下文
- context management：避免把所有历史内容塞进 prompt
- tool execution：调用受控工具完成 repo 读取、patch 规划、日志分析
- long-term memory：后续沉淀实验经验和失败教训
```

但 Deep Agents 不能替代 AutoAD Core。它是通用 agent harness，不天然理解异常检测科研协议，也不天然保证实验公平性、数据划分、评价脚本一致性和 baseline 可比性。

因此采用如下原则：

> **Deep Agents 可以执行任务，但不能定义科研闭环的事实源。**

事实源必须是：

```text
runs/{run_id}/
  input_task.yaml
  paper_summary.json
  transfer_report.json
  experiment_plan.json
  patch_plan.json
  approval_*.json
  run_command.sh
  stdout.log
  stderr.log
  metrics.json
  validity_report.json
  reflection.md
  final_report.md
  events.jsonl
  llm_calls.jsonl
```

### 5.3 推荐技术栈

| 模块 | MVP 选择 | Deep Agents 版增强 | 后续可扩展 |
|---|---|---|---|
| 前端 | CLI + Gradio / Streamlit | 展示 DeepAgentsHarness 事件流与 artifact | Web 前端 + FastAPI |
| 后端 | Python pipeline | AutoAD Core + DeepAgentsHarness adapter | FastAPI 服务化 |
| Agent 编排 | SimplePipelineHarness | DeepAgentsHarness 处理长程任务 | LangGraph / Temporal / Managed Agents |
| 论文解析 | MinerU（主力）+ MarkItDown（辅助） | Paper Reader 可作为 Deep Agents 子任务 | 更多格式 + VLM 增强 |
| 模型调用 | 自定义 Model Gateway | Deep Agents 通过统一 ModelClient 接模型 | 多供应商模型路由 |
| 输出约束 | Pydantic / JSON Schema | Deep Agents 输出必须过 schema 校验 | 更严格 schema + eval |
| 状态管理 | runs/{run_id} + JSONL | Deep Agents workspace 同步到 runs/{run_id} | SQLite / PostgreSQL |
| 实验执行 | conda / Docker + subprocess whitelist | 禁止直接暴露默认 shell，封装为 AutoAD Runner Tool | 沙盒执行平台 |
| 日志追踪 | 文件系统 + JSONL | Deep Agents tool call / subtask 写入 events.jsonl | trace / eval / dashboard |
| 报告生成 | Markdown | Deep Agents 汇总 artifact 生成报告草稿 | Markdown + HTML + PDF |

### 5.4 核心设计原则

1. **LLM 只是组件，不是系统本身**；
2. **Deep Agents 是 harness backend，不是项目本体**；
3. **状态必须落盘，不能只存在对话上下文、agent memory 或虚拟文件系统中**；
4. **所有阶段产物必须写入 `runs/{run_id}/`**；
5. **所有关键输出必须通过 Pydantic / JSON Schema 校验**；
6. **实验必须可复现、可追踪、可审计**；
7. **代码修改必须以 diff / patch 形式展示**；
8. **关键节点必须有人确认**；
9. **Deep Agents 的执行工具必须经过 AutoAD 白名单封装**；
10. **模型、数据集、baseline、执行环境都必须可配置**；
11. **不确定的赛题平台信息不能写死**。

### 5.5 第一层代码结构建议

```text
autoad-researcher/
  README.md
  pyproject.toml
  config.example.yaml
  .env.example

  src/autoad/
    cli.py
    app.py
    config.py

    core/
      pipeline.py
      run_manager.py
      approval.py
      events.py
      artifacts.py
      errors.py

    harness/
      base.py                  # AgentHarness 抽象接口
      simple_pipeline.py        # SimplePipelineHarness
      deepagents_backend.py     # DeepAgentsHarness

    schemas/
      task.py
      paper.py
      transfer.py
      experiment.py
      patch.py
      run.py
      validity.py
      report.py

    model/
      client.py
      router.py
      prompts/

    services/
      intent_clarifier.py
      paper_reader.py
      transfer_judge.py
      experiment_planner.py
      patch_planner.py
      runner.py
      log_analyzer.py
      validity_supervisor.py
      reporter.py

    tools/
      autoad_file_tools.py
      autoad_runner_tools.py
      autoad_repo_tools.py
      autoad_report_tools.py

    storage/
      db.py
      repositories.py

    execution/
      sandbox.py
      command_whitelist.py

    evals/
      fixtures/
      smoke_tests.py

  workspace/
    papers/
    repos/
    datasets/

  runs/
```

### 5.6 `AgentHarness` 抽象接口

第一层应先定义统一接口，避免业务代码直接依赖 Deep Agents 的具体 API。

```python
class AgentHarness:
    def run_stage(self, run_id: str, stage: str) -> str:
        """执行指定阶段，并返回写入的 artifact 路径。"""
        raise NotImplementedError
```

可选实现：

```text
SimplePipelineHarness
  - 用普通 Python service 串行执行
  - 用于 mock、测试、离线运行、稳定闭环

DeepAgentsHarness
  - 用 Deep Agents 处理规划、文件操作、子任务和长程上下文
  - 所有输出回写 AutoAD artifact
  - 所有工具调用写入 events.jsonl
```

### 5.7 DeepAgentsHarness 的硬边界

DeepAgentsHarness 必须遵守以下约束：

```text
1. 不直接修改真实实验代码，必须先生成 patch_plan 或 patch.diff；
2. 不直接运行任意 shell，必须通过 AutoAD Runner Tool；
3. 不直接覆盖 runs/{run_id} 中已有关键 artifact；
4. 不跳过 approval checkpoint；
5. 不自行宣布实验有效，必须经过 ValiditySupervisor；
6. 不把 agent memory 当成唯一状态源；
7. 不生成未通过 schema 校验的阶段产物；
8. 不修改 evaluation script、dataset split、baseline result，除非人工明确审批。
```

### 5.8 Deep Agents 本地接入 spike

你准备把 Deep Agents 弄到本地开发环境里，建议先做一个 2 天 spike，不要一开始就全量重构。

目标：

```text
输入：
  runs/run_demo/input_task.yaml
  runs/run_demo/paper_summary.json
  一个 toy repo 或 anomalib repo 摘要

任务：
  由 DeepAgentsHarness 生成 experiment_plan.json
  由 DeepAgentsHarness 生成 patch_plan.json
  由 DeepAgentsHarness 生成 final_report.md 草稿

限制：
  不允许真实修改代码
  不允许自由 shell
  必须写入 runs/run_demo/
  必须输出符合 schema 的 JSON
```

验收标准：

```text
1. 能稳定读取 runs/{run_id}/ 中的 artifact；
2. 能写出符合 schema 的 experiment_plan.json；
3. 能生成 patch_plan.json；
4. 能把执行过程写入 events.jsonl；
5. 工具调用路径可控；
6. 出错时能被 AutoAD Core 捕获；
7. Deep Agents 替换为 SimplePipelineHarness 后，主流程仍能运行。
```

如果 spike 失败，不影响主线；继续用 SimplePipelineHarness 跑 MVP。如果 spike 成功，再把 DeepAgentsHarness 纳入 P0/P1。
---

## 6. 功能模块设计

## 6.1 Intent Clarifier：意图澄清模块

目标：防止 Agent 基于不完整意图自行执行。

当用户输入：

```text
我想把这篇论文迁移到异常检测。
```

系统应先追问：

```text
1. 你关注哪类异常检测任务？
   - 工业图像异常检测
   - 时间序列异常检测
   - 视频异常检测
   - 日志异常检测
   - 多模态异常检测

2. 你希望优先验证什么？
   - 快速 smoke test
   - 小规模 benchmark
   - 完整复现
   - 尝试提升指标

3. 当前可用 baseline 是什么？
   - PatchCore
   - PaDiM
   - FastFlow
   - STFPM
   - 课题组已有模型

4. 当前计算资源是什么？
   - CPU
   - 单卡 GPU
   - 多卡 GPU
   - 组委会平台，资源未知
```

输出建议结构：

```yaml
clarification_status: incomplete
missing_information:
  - anomaly_detection_subfield
  - baseline
  - dataset
  - compute_budget
questions:
  - 你希望优先验证视觉异常检测、时间序列异常检测，还是其他方向？
  - 你是否已有指定 baseline？
```

说明：以上字段名是设计草案，后续应以实际代码中的 schema 为准。

---

## 6.2 Paper Reader：论文理解模块

### 底层解析引擎

论文解析需要高精度地从 PDF 中提取结构化信息。MVP 推荐两个互补引擎：

| 引擎 | 定位 | 适用场景 |
|---|---|---|
| **MinerU** | 高精度 PDF 解析引擎（公式→LaTeX、表格→HTML、109 语言 OCR） | 主力引擎：复杂论文 PDF，含公式、表格、多栏布局 |
| **MarkItDown** | 轻量多格式→Markdown 转换器 | 辅助引擎：快速处理 PPT/Word/HTML/EPUB 等非 PDF 格式 |

两者均已拉取到本地 `repos/MinerU/` 和 `repos/markitdown/`，可直接参考。

MVP 推荐用法：

```text
论文 PDF → MinerU 解析 → 结构化 Markdown（含公式 LaTeX + 表格 HTML）
非 PDF 格式 → MarkItDown 转换 → Markdown
→ 统一喂给 Paper Reader Agent 做信息抽取
```

输入：

```text
论文 PDF / 摘要 / arXiv 链接 / GitHub 链接
```

输出：

```yaml
task_type: 表征学习 / 生成模型 / 分割 / 检测 / 时序建模
core_idea: 待抽取
model_components:
  - backbone
  - loss
  - feature_fusion
training_data_assumption: 待抽取
requires_anomaly_labels: true_or_false_or_unknown
datasets:
  - 待抽取
metrics:
  - 待抽取
code_available: true_or_false_or_unknown
potential_transfer_points:
  - 特征提取
  - 异常分数
  - 重建模块
```

重点：

> Paper Reader 不是普通摘要器，而是为后续实验迁移服务的信息抽取器。解析质量直接决定下游迁移判断的准确性，因此 P0 阶段应优先选用 MinerU 作为主力 PDF 解析引擎。

---

## 6.3 Transferability Judge：可迁移性判断模块

判断维度：

| 维度 | 问题 |
|---|---|
| 数据假设 | 是否需要有标签异常样本？是否适合 one-class / unsupervised setting？ |
| 方法结构 | 可迁移的是 backbone、loss、feature fusion、memory bank、reconstruction 还是 anomaly score？ |
| 实现难度 | 是否能在现有 baseline 上低成本改动？ |
| 计算成本 | 是否适合单卡、小样本、快速验证？ |
| 指标兼容 | 是否能对齐 image AUROC、pixel AUROC、PRO、F1 等指标？ |
| 科学有效性 | 是否可能引入评价泄漏、协议不一致或 shortcut？ |

输出示例：

```text
可迁移等级：中

建议迁移位置：
- 多尺度特征融合模块
- anomaly score 计算方式

主要风险：
- 原论文需要部分标注数据，和无监督异常检测设定不完全一致；
- 新模块可能增加显存占用；
- 需要确认评价协议是否和 baseline 一致。

建议最小实验：
- baseline：PatchCore
- dataset：MVTec AD 的 1–2 个类别
- metric：image AUROC + pixel AUROC
- mode：smoke test
```

---

## 6.4 Experiment Planner：实验规划模块

目标：把论文想法转成实验计划。

输出内容：

```yaml
experiment_goal: 验证论文中的某模块是否能提升视觉异常检测效果
baseline: 待定
method_variant: 待定
dataset: 待定
categories:
  - 待定
metrics:
  - image_auroc
  - pixel_auroc
control_group:
  method: original_baseline
experiment_group:
  method: baseline_with_transferred_module
resource_budget:
  gpu: 单卡或未知
  mode: smoke_test
expected_effect:
  - 提升特征表达
  - 降低误检
risks:
  - 特征维度不匹配
  - 显存增加
  - 指标不可比
```

说明：这里的字段是建议 schema，后续应根据实际代码确定，不应在没有实现前对外宣称已经固定。

---

## 6.5 Code Agent：代码修改模块

目标：生成可审查、可回滚的代码修改建议。

不建议第一版宣传“全自动改代码”。更稳妥的表述是：

> 人机协同的科研代码改写智能体。

流程：

```text
读取 repo 结构
→ 定位 baseline 相关文件
→ 生成修改计划
→ 生成 patch / diff
→ 人工确认
→ 应用 patch
→ 运行 smoke test
→ 失败则回滚或生成修复建议
```

Code Agent 输出：

```text
计划修改：
1. 新增模块：xxx
2. 修改 baseline 的特征提取逻辑
3. 修改配置文件，增加 method_variant
4. 新增实验入口或参数

风险：
1. 特征维度可能不匹配
2. 运行时间可能增加
3. 指标读取脚本可能需要适配

需要人工确认：是
```

---

## 6.6 Runner Agent：实验运行模块

目标：在受控环境中运行实验。

记录内容：

```yaml
command: 待记录
environment: 待记录
code_version: 待记录
config_path: 待记录
stdout_log: 待记录
stderr_log: 待记录
metrics_path: 待记录
status: success_or_failed
```

执行原则：

- 只运行白名单命令；
- 不覆盖旧实验结果；
- 每一次运行生成独立实验目录；
- 保存 stdout、stderr、config、metrics；
- 失败后保留错误栈；
- 不自动删除数据和模型文件。

---

## 6.7 Log Analyzer：日志与指标分析模块

输入：

```text
stdout.log
stderr.log
metrics.json
config.yaml
baseline_metrics.json
```

输出：

```text
实验状态：成功 / 失败
主要指标：image AUROC、pixel AUROC、PRO、F1
与 baseline 对比：提升 / 下降 / 不显著
异常情况：报错、显存不足、指标缺失、训练未完成
```

失败时输出：

```text
失败类型：环境错误 / 代码错误 / 数据路径错误 / 指标解析错误 / 训练发散 / 显存不足
可能原因：...
建议修复：...
是否需要升级到强模型分析：是 / 否
```

---

## 6.8 Scientific Validity Supervisor：科学有效性监督模块

目标：避免出现“看似提升，实际无效”的实验结论。

检查点：

1. 是否和 baseline 使用同一数据划分；
2. 是否使用了异常样本标签；
3. 是否改变了评价协议；
4. 是否只挑选了有利类别汇报；
5. 是否发生数据泄漏；
6. 是否运行了足够的对照实验；
7. 是否只是增加参数量带来的偶然提升；
8. 是否需要 ablation 验证贡献来源。

输出示例：

```text
科学有效性判断：暂不充分

原因：
1. 当前只在一个类别上验证；
2. 没有控制参数量增加带来的影响；
3. 没有验证 pixel-level 指标是否采用同一后处理流程。

下一步建议：
1. 至少增加 2–3 个类别；
2. 添加 only-feature-fusion ablation；
3. 固定 post-processing 参数；
4. 保存完整 config 和 evaluation script 版本。
```

---

## 6.9 Report Agent：报告生成模块

输出 Markdown 实验报告。

报告结构：

```markdown
# 实验报告

## 1. 任务目标

## 2. 输入论文 / 方法来源

## 3. 论文核心方法

## 4. 可迁移性判断

## 5. 实验设计

## 6. 代码修改摘要

## 7. 运行环境与命令

## 8. 实验结果

## 9. 与 baseline 对比

## 10. 失败原因或有效性分析

## 11. 下一轮实验建议

## 12. 附录：日志、配置、指标文件路径
```

---

## 7. 多模型路由与成本控制

### 7.1 设计原则

不要把所有任务都交给最强模型。应设计一个模型网关：

```text
Model Gateway
  ├── light_model：低成本任务
  ├── strong_model：高风险任务
  ├── embedding_model：检索任务
  └── local_model：可选离线任务
```

模型名、API Key、base URL、temperature、max tokens 等都应写在配置文件或环境变量中，不应硬编码。

### 7.2 路由策略

| 任务 | 推荐策略 |
|---|---|
| 普通论文摘要 | 轻量模型 |
| 论文结构化抽取 | 轻量模型 + schema 校验 |
| 可迁移性初判 | 轻量模型 |
| 高风险迁移判断 | 强模型 |
| 实验计划生成 | 强模型 |
| 代码 patch 生成 | 强模型 |
| 日志归纳 | 轻量模型 |
| 报错定位 | 先轻量模型，失败后强模型 |
| 最终科研结论 | 强模型 |
| 答辩材料生成 | 强模型 |

### 7.3 缓存策略

缓存重点：

```text
稳定前缀：
- 系统提示词
- 工具定义
- 项目说明
- 异常检测任务协议
- benchmark 说明
- 实验评价标准
- 代码仓库结构摘要

动态后缀：
- 当前用户输入
- 当前工具输出
- 当前实验日志
- 当前代码 diff
```

避免破坏缓存：

```text
不要把以下内容放进稳定前缀：
- 当前时间
- 随机 session id
- 每轮变化的文件树
- 完整训练日志
- 每次顺序不同的工具列表
- 临时路径
- 每轮不稳定摘要
```

---

## 8. 状态管理与文件结构

系统状态不能只保存在对话里，也不能只保存在 Deep Agents 的 agent memory 或 virtual filesystem 里。AutoAD 的唯一事实源必须是 `runs/{run_id}/` 与结构化 artifact。

### 8.1 推荐 run 目录

```text
runs/{run_id}/
  input_task.yaml
  task_state.json

  paper_summary.json
  transfer_report.json
  experiment_plan.json

  approval_plan.json
  patch_plan.json
  patch.diff
  approval_patch.json

  run_command.sh
  stdout.log
  stderr.log
  metrics.json
  run_result.json

  validity_report.json
  reflection.md
  final_report.md

  events.jsonl
  llm_calls.jsonl
```

### 8.2 Deep Agents workspace 与 AutoAD artifact 的关系

```text
Deep Agents workspace：临时草稿、上下文工作区、子任务中间产物
AutoAD runs/{run_id}：审计事实源、评审材料、恢复依据、最终报告来源
```

约束：

```text
1. Deep Agents 可以在自己的 workspace 中规划和试写；
2. 进入下一阶段前，必须把结果同步为 AutoAD artifact；
3. 同步后的 artifact 必须通过 schema 校验；
4. 后续模块只能依赖 AutoAD artifact，不能依赖未同步的内部记忆；
5. 每次工具调用、文件写入、命令执行都必须写入 events.jsonl。
```

### 8.3 每次实验至少保存

1. 输入论文或方法想法；
2. 用户约束；
3. 论文结构化理解；
4. 迁移判断；
5. 实验计划；
6. 代码修改计划或 diff；
7. 人工审批记录；
8. 运行命令；
9. 环境信息；
10. stdout / stderr；
11. 指标；
12. 科学有效性检查；
13. 最终分析报告。

说明：以上路径和字段是当前设计草案。正式编码时应以 `schemas/` 和 `core/artifacts.py` 中的实现为准，不应在其他模块临时发明字段名。
---

## 9. 安全边界

### 9.1 允许操作

```text
- 读取项目目录
- 读取指定论文文件
- 读取指定实验代码
- 修改指定实验目录
- 生成 patch
- 运行白名单 Python 脚本
- 读取日志
- 生成报告
```

### 9.2 禁止操作

```text
- 删除数据集
- 删除整个项目
- 访问系统敏感路径
- 上传代码到外部
- 安装未知包
- 执行危险 shell 命令
- 覆盖 baseline 结果
- 未经确认直接跑大规模训练
```

### 9.3 Deep Agents 工具边界

如果使用 DeepAgentsHarness，必须封装默认工具能力：

```text
默认文件工具 → AutoADFileTool
默认 shell / execute → AutoADRunnerTool
默认 repo 读取 → AutoADRepoTool
默认报告写入 → AutoADReportTool
```

其中 `AutoADRunnerTool` 必须执行以下检查：

```text
1. 命令是否在白名单内；
2. 是否访问允许的 workspace；
3. 是否尝试删除或覆盖数据集；
4. 是否修改 evaluation script；
5. 是否覆盖 baseline 结果；
6. 是否已经通过运行命令审批；
7. stdout / stderr 是否写入当前 run 目录；
8. command 是否记录到 run_command.sh。
```

### 9.4 人工确认点

至少设置 5 个确认点：

1. 任务目标确认；
2. 实验方案确认；
3. 代码修改计划确认；
4. patch / diff 确认；
5. 实验执行命令确认。

---

## 10. Demo 设计

### 10.1 Demo 1：成功迁移案例

输入：

```text
我想把这篇表征学习论文的方法迁移到工业图像异常检测。
```

系统输出：

```text
论文核心方法：多尺度特征融合 / 对比学习 / 特征归一化
可迁移点：替换 PatchCore 的特征提取或特征融合模块
实验设置：MVTec AD 的 1–2 个类别
指标：image AUROC、pixel AUROC
结果：与 baseline 对比
结论：是否值得继续扩大实验
```

### 10.2 Demo 2：拒绝迁移案例

输入：

```text
一篇依赖大量有标签异常样本的监督方法论文。
```

系统输出：

```text
不建议直接迁移。

原因：
1. 原方法依赖异常标签；
2. 当前目标任务默认只有正常样本训练；
3. 直接迁移会改变任务设定，和 baseline 不公平。

替代建议：
只迁移其中的 self-supervised pretext task 或 backbone 初始化策略。
```

### 10.3 Demo 3：失败反思案例

输入：

```text
实验结果没有提升。
```

系统输出：

```text
可能原因：
1. 新模块增加参数但训练样本不足；
2. 特征分布对正常类过拟合；
3. anomaly score 与原 baseline 不兼容；
4. 后处理参数可能未对齐。

下一轮建议：
1. 固定 backbone，只测试 anomaly score；
2. 减小 feature dimension；
3. 增加类别数量；
4. 做 ablation。
```

---

## 11. 实现优先级

### P0：必须完成

```text
1. 建立 AutoAD Core：run_id、schemas、artifact store、events.jsonl
2. 建立 SimplePipelineHarness：保证不依赖 Deep Agents 也能跑通闭环
3. 建立 DeepAgentsHarness spike：只处理 experiment_plan、patch_plan、report 草稿
4. 用户输入论文 / 方法想法
5. MinerU 解析论文 PDF → 结构化 Markdown（公式、表格、布局）
6. Agent 主动追问实验目标
7. 输出结构化迁移判断
8. 输出实验计划并通过 schema 校验
9. 生成代码修改建议或伪 patch
10. 人工确认实验方案和 patch 计划
11. 运行一个固定 benchmark 或 smoke test
12. 读取结果并生成报告
```

### P1：最好完成

```text
1. DeepAgentsHarness 扩展到 PatchPlanner / LogAnalyzer / Reporter
2. 模型路由
3. 缓存命中统计
4. 实验日志归档
5. 代码 diff 展示
6. 失败自动归因
7. 科学有效性检查
8. Deep Agents 子任务事件流可视化
9. Deep Agents workspace 与 runs/{run_id} 的同步机制
```

### P2：有时间再做

```text
1. 多论文批量筛选
2. 自动搜索最新论文
3. 自动生成 ablation
4. 多 Agent 并行执行
5. 自动修复复杂环境错误
6. 自动生成完整论文草稿
7. Deep Agents long-term memory / skill 沉淀
8. 接入 OpenHands / CodeBuddy / Claude Agent SDK 作为替代 harness backend
9. 视比赛平台情况接入 CNB / Temporal / Managed Agent Platform
```

优先级原则：

> 先闭环，再复杂化。

---

## 12. 和老师讨论时的问题清单

建议重点问老师以下问题：

1. **方向是否认可？**  
   是否可以把项目定位为“面向异常检测的 Dry Lab 科研智能体”？

2. **异常检测子方向选哪个？**  
   视觉异常检测、工业缺陷检测、时间序列异常检测，哪个最贴合课题组资源？

3. **baseline 用什么？**  
   使用 Anomalib、PatchCore、PaDiM、FastFlow，还是课题组已有代码？

4. **数据集用什么？**  
   使用 MVTec AD、VisA，还是课题组内部数据？如果是内部数据，参赛展示是否合规？

5. **Demo 做到什么程度？**  
   必须真实跑出实验指标，还是先展示半自动闭环即可？

6. **组委会平台如何提交？**  
   是否需要 Docker？是否能联网？是否能调用外部 API？是否能使用 GPU？是否要求开源代码？

7. **队伍配置是否足够？**  
   至少需要一个人负责 Agent / 后端，一个人负责异常检测实验，一个人负责前端展示和材料。

8. **Deep Agents 是否适合作为第一层执行内核？**  
   建议先做本地 spike，让 DeepAgentsHarness 只负责 experiment_plan、patch_plan 和 report 草稿，验证其对长程任务、文件系统和子任务分发的帮助是否大于集成成本。

---

## 13. 对老师的汇报口径

可以这样说：

> 老师，我们想报 AI for Science 里的干闭环方向，不做泛化的通用 Agent，而是聚焦异常检测科研流程。项目暂定叫 AutoAD-Researcher，目标是把“论文理解—方法迁移—实验规划—代码修改—实验运行—结果反思”做成一个半自动闭环。
>
> 第一版不会追求全自动发现 SOTA，只做一个可演示 MVP：输入一篇论文或一个方法想法，系统判断它是否适合迁移到异常检测，生成最小实验方案，在固定 baseline 和数据集上跑 smoke test，然后读取日志和指标，输出实验报告。
>
> 技术上我们不一开始绑定复杂 Agent 框架，而是按经典软件工程实现：LLM 作为组件，状态用 SQLite / JSONL 管理，实验用 Docker 或 conda 加 subprocess 白名单执行，输出用 Pydantic / JSON Schema 约束，代码修改以 diff / patch 形式展示，关键节点由人确认。
>
> 我们想请您判断三个问题：第一，这个方向是否适合参赛；第二，异常检测里应该选哪个 baseline 和数据集；第三，第一版 Demo 需要做到真实跑实验，还是先做到可追溯的半自动闭环即可。

---

## 14. 风险与应对

| 风险 | 说明 | 应对 |
|---|---|---|
| 目标过大 | 全自动科研系统很难短期完成 | 收缩到视觉异常检测 + 一个 baseline + 一个闭环 |
| 组委会平台未知 | 不清楚能否联网、能否调用外部 API、是否支持 Docker | 做 CLI、Docker、离线 mock、配置化模型接口 |
| 实验难跑通 | 异常检测环境和数据依赖复杂 | 优先用已有 repo 和小规模 smoke test |
| Agent 幻觉 | 可能生成错误实验方案或错误结论 | schema 约束、人工确认、日志追踪、科学有效性检查 |
| Deep Agents 过度接管状态 | 状态散落在 agent memory 或 virtual filesystem 中，难以审计 | AutoAD Core 规定唯一事实源为 `runs/{run_id}`，Deep Agents 只做 backend |
| Deep Agents 默认执行能力风险 | 默认 execute / 文件工具可能绕过实验安全边界 | 封装 AutoADRunnerTool / AutoADFileTool，所有命令走白名单和审批 |
| 代码安全 | Agent 可能执行危险命令 | subprocess 白名单、沙盒、patch 审批 |
| 指标不公平 | 改变协议导致虚假提升 | 固定 evaluation script，保存 config，加入 Supervisor |
| 合规风险 | 参考非公开源码存在问题 | 只参考公开产品交互和开源项目，不使用泄露源码 |

---

## 15. 最终判断

建议参赛，但必须控制边界。

推荐最终定位：

> **AutoAD-Researcher：面向异常检测的文献迁移与实验闭环智能体。**

核心卖点：

1. **异常检测垂直化**：不是泛 Agent，而是面向真实科研方向；
2. **Dry Lab 闭环**：不是文献助手，而是推进到实验和结论；
3. **人机协同**：关键节点主动追问和确认，避免 Agent 自作主张；
4. **实验可追溯**：保存 config、patch、command、log、metrics、report；
5. **长程任务能力**：通过 DeepAgentsHarness 探索 planning、virtual filesystem、subagents、context management；
6. **成本可控**：模型路由、缓存稳定前缀、轻重模型分工；
7. **科学有效性约束**：检查数据泄漏、评价协议、baseline 公平性和 ablation。

一句话结论：

> 第一阶段只做视觉异常检测一个方向、一个 baseline、一个数据集、一个最小闭环。技术上采用 AutoAD Core 作为领域闭环控制层，先用 SimplePipelineHarness 保底跑通，再用 DeepAgentsHarness 做长程任务增强。先证明系统能把论文想法推进到实验结论，再考虑扩展到多论文、多数据集、多 Agent。
