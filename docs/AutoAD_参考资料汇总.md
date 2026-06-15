# AutoAD-Researcher 参考资料汇总

> 版本：2026-06-13  
> 用途：AI for Science / Dry Lab / 异常检测科研闭环智能体项目的资料库草案  
> 当前策略：**先不采用 LangGraph 作为主线**，优先采用经典软件工程方式实现：LLM 作为组件，数据库管理状态，队列/Temporal 管长任务，类型系统与 schema 管输出，trace/eval/log 管质量。

---

## 0. 当前项目定位

建议项目定位为：

> **AutoAD-Researcher：面向异常检测的文献迁移与实验闭环智能体**

核心任务不是做“通用 Claude Code 替代品”，也不是复刻完整 AutoSOTA，而是做一个垂直科研场景闭环：

```text
论文 / 方法想法 / 实验目标
→ 意图澄清
→ 论文结构化解析
→ 异常检测迁移可行性判断
→ 实验方案生成
→ 代码 patch / config 修改建议
→ 人工确认
→ 执行 smoke test / benchmark
→ 读取日志和指标
→ 科研有效性检查
→ 结果反思
→ 输出实验报告和下一轮建议
```

第一版应收缩为：

```text
方向：视觉异常检测
数据集：MVTec AD 的 1-2 个类别
baseline：PatchCore / PaDiM / FastFlow / STFPM / 组内已有模型
目标：给定一篇论文，判断是否可迁移，并跑出一个最小实验闭环
```

---

## 1. 架构原则：先不用黑盒 Agent 框架

当前判断：**不要把项目绑定到复杂黑盒 Agent 框架。**

更稳的实现思路：

| 层级 | 推荐做法 |
|---|---|
| LLM 调用 | 当成普通服务组件，通过统一 `ModelClient` 封装 |
| 状态管理 | SQLite / PostgreSQL + 文件工作区 |
| 长任务 | 先用本地任务队列；后续可接 Temporal |
| 输出约束 | Pydantic / JSON Schema / TypedDict |
| 实验资产 | `runs/{run_id}/` 文件夹持久化 |
| 日志 | JSONL + trace_id + run_id |
| 评测 | fixture tests + smoke tests + regression eval |
| UI | Gradio / Streamlit 作为 Demo 外壳，CLI 作为核心入口 |
| 沙盒 | Docker / conda env / subprocess whitelist |
| 人工确认 | 明确的 approval checkpoint，不依赖 Agent 自觉 |

### 1.1 推荐最小目录结构

```text
autoad-researcher/
  README.md
  pyproject.toml
  config.example.yaml
  .env.example

  src/autoad/
    app.py                         # FastAPI/Gradio 可选入口
    cli.py                         # 核心 CLI 入口
    config.py
    schemas/
      task.py
      paper.py
      experiment.py
      patch.py
      run.py
      report.py
    model/
      client.py
      router.py
      prompts/
    services/
      paper_reader.py
      transfer_judge.py
      experiment_planner.py
      code_patch_planner.py
      runner.py
      log_analyzer.py
      validity_supervisor.py
      reporter.py
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
    {run_id}/
      input_task.yaml
      paper_summary.json
      transfer_report.json
      experiment_plan.json
      approval.json
      patch.diff
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

### 1.2 平台未知时的兼容策略

目前还不清楚组委会测试平台的形式，不能写死实现。需要预留三种入口：

| 可能平台形态 | 应对方式 |
|---|---|
| 只收项目材料 / 代码仓库 | 提供 README、演示视频、最小数据样例、可复现实验脚本 |
| 提供容器运行平台 | 提供 Dockerfile、`run.sh`、CLI 参数、离线小样例 |
| 提供在线交互平台 | 提供 FastAPI / Gradio 服务入口，并保留 CLI |
| 要求模型 API 可配置 | 所有模型名、API key、base_url 放到配置文件和环境变量 |
| 无法联网 | 准备 mock LLM / 小样例缓存 / 离线报告模式 |
| 有固定评测接口 | 在 `src/autoad/adapters/` 下新增平台适配层，不改核心逻辑 |

必须向组委会确认的事项：

```text
1. 提交形式：PPT / PDF / 代码仓库 / Docker 镜像 / 在线服务？
2. 测试平台是否联网？
3. 是否允许调用外部模型 API？
4. 是否提供 GPU？
5. 是否允许安装依赖？
6. 是否有统一输入输出接口？
7. 是否要求固定运行时间？
8. 是否要求开源全部代码？
9. 是否允许访问本地文件系统？
10. 是否有安全审查或沙盒限制？
```

在没确认前，不要绑定某个框架、云平台或模型服务。

---

## 2. P0 参考论文：长周期科研闭环系统

这些是最重要的论文，直接影响你们的项目叙事和系统设计。

### 2.1 AutoLab: Can Frontier Models Solve Long-Horizon Auto Research and Engineering Tasks?

- BibTeX key: `xu2026autolabfrontiermodelssolve`
- arXiv: https://arxiv.org/abs/2606.05080
- GitHub: https://github.com/autolabhq/autolab
- 项目页： https://autolab.moe/

核心内容：

AutoLab 是一个长周期闭环优化 benchmark，强调智能体不是一次性回答问题，而是在固定时间预算内不断：

```text
benchmark → edit → run → measure → reflect → edit again
```

它包含 36 个专家设计任务，覆盖：

```text
1. system optimization
2. puzzle / challenge
3. model development
4. CUDA kernel optimization
```

对 AutoAD-Researcher 的启发：

| AutoLab 设计 | 对本项目的迁移 |
|---|---|
| 从正确但低性能 baseline 开始 | 从 PatchCore/PaDiM baseline 开始 |
| 固定 wall-clock budget | 每轮实验限制时间和显存 |
| 强调 empirical feedback | 每轮读取指标和日志后再决策 |
| benchmark harness | 建立 `AD-AgentBench` 小型评测 |
| 任务资产开源 | 每个 demo case 保留完整 `runs/` 资产 |

建议吸收：

```text
- 不追求一次性生成完美方案
- 强调持续迭代能力
- 每轮必须有可量化指标
- 每轮必须保存实验痕迹
```

---

### 2.2 AutoSOTA: An End-to-End Automated Research System for State-of-the-Art AI Model Discovery

- BibTeX key: `li2026autosotaendtoendautomatedresearch`
- arXiv: https://arxiv.org/abs/2604.05550
- GitHub: https://github.com/tsinghua-fib-lab/AutoSOTA

核心内容：

AutoSOTA 是完整的自动科研优化系统，目标是从论文到可复现代码，再进一步优化出新的 SOTA。论文将流程拆成三个阶段：

```text
1. resource preparation and goal setting
2. experiment evaluation
3. reflection and ideation
```

并使用 8 个 specialized agents：

```text
AgentResource
AgentObjective
AgentInit
AgentMonitor
AgentFix
AgentIdeator
AgentScheduler
AgentSupervisor
```

对 AutoAD-Researcher 的启发：

| AutoSOTA 模块 | 本项目对应模块 |
|---|---|
| AgentResource | 论文 / repo / dataset 解析 |
| AgentObjective | 异常检测评价协议构建 |
| AgentInit | 实验环境初始化 |
| AgentMonitor | 训练与测试过程监控 |
| AgentFix | 报错修复建议 |
| AgentIdeator | 迁移想法生成 |
| AgentScheduler | 实验优先级调度 |
| AgentSupervisor | 科研有效性监督 |

必须吸收的思想：

```text
- 自动化科研不等于盲目优化指标
- 必须防止 evaluation leakage
- 必须检查实验协议是否被破坏
- 必须记录失败实验，而不是只保留成功结果
```

不建议第一版照搬：

```text
- 不做全自动 SOTA 发现
- 不做多领域通用论文复现
- 不承诺平均几小时超过原论文
```

---

### 2.3 Toward Autonomous Long-Horizon Engineering for ML Research

- BibTeX key: `chen2026autonomouslonghorizonengineeringml`
- arXiv: https://arxiv.org/abs/2604.13018

核心内容：

这篇论文提出 AiScientist 系统。最值得关注的是：

> **File-as-Bus**

意思是：不要把长周期任务的状态只放在聊天上下文里，而是用文件工作区作为跨 Agent、跨调用、跨时间的 durable state。

核心观点：

```text
thin control over thick state
```

也就是：控制逻辑尽量薄，真正的任务状态放在持久化 artifact 里。

对 AutoAD-Researcher 的启发非常直接：

```text
runs/{run_id}/plan.md
runs/{run_id}/patch.diff
runs/{run_id}/metrics.json
runs/{run_id}/reflection.md
runs/{run_id}/final_report.md
```

建议吸收：

```text
- 不依赖超长对话历史保存状态
- 每个阶段输出一个结构化 artifact
- 后续模块必须从 artifact 重新读取事实
- LLM 只处理当前局部任务，不背负全部上下文
```

这是最符合你提出的“回归经典软件工程”的论文之一。

---

### 2.4 Claw AI Lab: An Autonomous Multi-Agent Research Team

- BibTeX key: `wu2026clawailabautonomous`
- arXiv: https://arxiv.org/abs/2605.22662
- GitHub: https://github.com/Claw-AI-Lab/Claw-AI-Lab

核心内容：

Claw AI Lab 强调 interactive AI laboratory，而不是隐藏式 prompt-to-paper pipeline。它支持：

```text
- customizable roles
- collaborative workflows
- real-time monitoring
- artifact inspection
- rollback / resume
- Claw-Code Harness
```

对 AutoAD-Researcher 的启发：

| Claw AI Lab | 本项目可吸收 |
|---|---|
| Dashboard | Gradio / Streamlit 展示运行状态 |
| artifact inspection | 每轮展示 plan、diff、metrics、logs |
| rollback / resume | 每轮 run_id 可恢复 |
| Claw-Code Harness | 本地 repo / dataset / checkpoint 接入 |
| interactive lab | 用户在关键点确认 |

建议吸收：

```text
- 把系统做成“可检查的科研工作台”
- 不把 Agent 决策藏在黑盒里
- 每个阶段产物都可下载、可复查、可回滚
```

---

### 2.5 AutoScientists: Self-Organizing Agent Teams for Long-Running Scientific Experimentation

- BibTeX key: `gao2026autoscientistsselforganizingagentteams`
- arXiv: https://arxiv.org/abs/2605.28655
- GitHub: https://github.com/mims-harvard/AutoScientists

核心内容：

AutoScientists 关注 decentralized / self-organizing agent teams。它不是单一 central planner，而是让 agent 围绕 promising hypotheses 自组织成团队，批判实验提案，分享成功和失败，减少重复探索。

对 AutoAD-Researcher 的启发：

```text
- 长期可做多假设并行探索
- 每个假设都有独立实验记录
- 失败方向也要进入 knowledge base
- 运行前先 critique 实验方案，减少无效 compute
```

不建议第一版做：

```text
- 不要第一版就做复杂多 Agent 自组织
- 不要第一版并行跑大量实验
```

适合作为 P1/P2 扩展。

---

### 2.6 AutoFigure: Generating and Refining Publication-Ready Scientific Illustrations

- BibTeX key: `zhu2026autofiguregeneratingrefiningpublicationready`
- arXiv: https://arxiv.org/abs/2602.03828
- GitHub: https://github.com/ResearAI/AutoFigure
- 项目页： https://autofigure.org/

核心内容：

AutoFigure 关注从论文长文本生成和迭代优化 scientific illustration，尤其是 publication-ready figures。

对 AutoAD-Researcher 的启发：

```text
- 后期可以自动生成方法流程图
- 后期可以自动生成实验对比图
- 后期可以把 final_report.md 转成答辩图表
```

优先级：

```text
P2：不是闭环核心，不影响第一版
```

---

## 3. P1 参考系统：自动科研与实验评测

### 3.1 karpathy/autoresearch

- GitHub: https://github.com/karpathy/autoresearch

核心内容：

Karpathy 的 autoresearch 是极简自动实验循环。它让 Agent 在一个小型训练项目里反复修改代码、训练、检查结果、保留或丢弃改动。

对 AutoAD-Researcher 的启发：

```text
- 范围小
- 指标明确
- 实验循环短
- 每次只改有限文件
- 人类通过说明文件定义研究协议
```

建议吸收：

```text
research_protocol.md
- 任务定义
- baseline
- dataset
- metric
- 可修改范围
- 禁止操作
- 每轮输出格式
```

---

### 3.2 Agent Laboratory

- 项目页： https://agentlaboratory.github.io/

核心内容：

Agent Laboratory 的定位是 human-produced research idea → literature review → experimentation → report writing。它强调：

```text
You are the pilot.
```

对 AutoAD-Researcher 的启发：

```text
- 人类是 pilot，Agent 是科研助手
- 不宣传完全无人科研
- 保留关键确认点
- 把文献、实验、报告串起来
```

---

### 3.3 The AI Scientist

- GitHub: https://github.com/SakanaAI/AI-Scientist

核心内容：

The AI Scientist 更激进，目标是 idea generation、literature search、experiment planning、experiment execution、paper writing、reviewing。

对本项目的价值：

```text
- 作为远期愿景参考
- 学习 template-based research
- 学习安全警告与容器化要求
```

不建议第一版采用：

```text
- 不承诺自动写完整论文
- 不承诺无人发现新方向
- 不允许 Agent 随意执行未知代码
```

---

### 3.4 MLAgentBench

- arXiv: https://arxiv.org/abs/2310.03302

核心内容：

MLAgentBench 评估 Agent 是否能完成机器学习实验任务，包括读写文件、执行代码、检查输出和调整方案。

对 AutoAD-Researcher 的启发：

可以定义自己的小型 `AD-AgentBench`：

```text
Task 1: 跑通 PatchCore + MVTec bottle
Task 2: 修改 backbone 配置并比较 image AUROC
Task 3: 给一篇不适合异常检测的论文，要求 Agent 拒绝迁移
Task 4: 故意制造环境错误，让 Agent 生成修复建议
Task 5: 给一次失败实验，让 Agent 分析失败原因
```

---

### 3.5 PaperBench

- OpenAI page: https://openai.com/index/paperbench/

核心内容：

PaperBench 评估 Agent 复现 AI 论文的能力，重点不是只看最终结果，而是分层 rubric 评价论文理解、代码实现、实验复现等子任务。

对 AutoAD-Researcher 的启发：

```text
- 设计分层评分
- 不只看 AUROC
- 还看计划质量、patch 质量、日志完整性、结论可靠性
```

建议评分：

```text
论文理解：20%
迁移判断：20%
实验计划：20%
代码 / config 修改：20%
日志与结果分析：20%
```

---

### 3.6 CORE-Bench

- arXiv: https://arxiv.org/abs/2409.11363

核心内容：

CORE-Bench 聚焦 computational reproducibility。它强调 Agent 要先能复现实验，而不是直接创造新发现。

对 AutoAD-Researcher 的启发：

```text
第一阶段目标：
- 跑通 baseline
- 复现实验协议
- 记录日志
- 稳定输出指标

第二阶段目标：
- 做论文方法迁移
- 做小规模 ablation
- 做失败反思
```

---

## 4. 代码 Agent / 工程 Agent 参考仓库

这些不是项目本体，只作为代码修改、shell 执行、diff 展示、repo 交互的参考。

### 4.1 OpenHands / Software Agent SDK

- GitHub org: https://github.com/OpenHands
- SDK: https://github.com/OpenHands/software-agent-sdk/
- Docs: https://docs.openhands.dev/sdk

可参考点：

```text
- 软件 Agent 的工具接口
- 本地 workspace / Docker workspace
- 多 Agent 重构任务
- 代码仓库操作
- 执行环境隔离
```

对本项目的吸收：

```text
- 只允许 Agent 操作指定 repo
- 所有修改先生成 patch.diff
- 运行命令必须经过白名单
- stdout/stderr 必须保存
```

---

### 4.2 SWE-agent

- GitHub: https://github.com/swe-agent/swe-agent
- Docs: https://swe-agent.com/latest/

可参考点：

```text
- GitHub issue → inspect repo → edit code → run tests → submit patch
- 单 YAML 配置
- 适合作为研究工具
- agent-computer interface
```

迁移到本项目：

```text
paper/idea → inspect AD repo → edit config/code → run benchmark → submit report
```

---

### 4.3 mini-SWE-agent

- GitHub: https://github.com/SWE-agent/mini-swe-agent

可参考点：

```text
- 极简 agent loop
- 约 100 行核心思路
- 不依赖复杂框架
- 适合先实现 MVP
```

建议吸收：

```text
while not done:
    observe files/logs
    ask model for next action
    validate action
    execute allowed action
    record result
```

---

### 4.4 Aider

- Website: https://aider.chat/
- GitHub org: https://github.com/Aider-AI

可参考点：

```text
- 终端 pair programming
- codebase map
- git diff
- lint / test
- 多模型支持
```

对本项目的吸收：

```text
- repo summary
- patch.diff
- changed_files.json
- commit message 草稿
- test/smoke result
```

---

### 4.5 OpenCode

- Website: https://opencode.ai/
- GitHub: https://github.com/opencode-ai/opencode

可参考点：

```text
- 开源 coding agent
- terminal / IDE / desktop 产品形态
- 多 provider 模型连接
- session 管理
```

对本项目的吸收：

```text
- 多模型网关
- session/run 管理
- terminal-like execution UI
```

注意：OpenCode 适合做产品形态参考，不建议作为核心科研闭环底座。

---

### 4.6 Reasonix

- DeepSeek official integration page: https://api-docs.deepseek.com/quick_start/agent_integrations/reasonix

可参考点：

```text
- DeepSeek-native coding agent
- cache-first loop
- flash-first cost control
- automatic tool-call repair
- /pro 手动切换强模型
```

对本项目的吸收：

```text
- 默认便宜模型处理摘要、日志、追问
- 高风险任务升级强模型
- 记录 prompt_cache_hit_tokens / prompt_cache_miss_tokens
- 工具调用失败时走 repair loop
```

---

### 4.7 MiMoCode（小米）

- Website: https://mimo.xiaomi.com/en/mimocode
- 本地路径: `repos/mimo-code/`
- 参考分析: `references/coding-agents/README.md`

定位：

MiMoCode 是小米开源的终端原生 AI 编程助手，基于 OpenCode fork。最核心的差异化能力是**跨会话持久化记忆系统**，通过 SQLite FTS5 实现 MEMORY.md 的全量索引和检索。

技术栈：

```text
- TypeScript + Bun 运行时
- SQLite FTS5 全文搜索（持久化记忆）
- Drizzle ORM
- Tauri 桌面应用
- 基于 anomalco/opencode fork
```

架构亮点：

```text
packages/opencode/src/
├── agent/       → Agent 定义 (build / plan / compose 三种模式)
├── memory/      → 跨会话持久化记忆 (MEMORY.md + checkpoint.md)
├── session/     → 会话管理 + 智能上下文重建
├── task/        → 树形任务系统 (T1 → T1.1 → T1.2...)
├── tool/        → 工具系统
├── skill/       → 技能系统 (/dream 记忆提取, /distill 工作流自动化)
├── workflow/    → 工作流编排
├── provider/    → LLM 提供商适配层
├── mcp/         → MCP 协议集成
└── team/        → 子 Agent 系统（并行执行 + 生命周期管理）
```

五大差异化特性：

| 特性 | 说明 | 对 AutoAD 的启示 |
|---|---|---|
| **持久化记忆** | SQLite FTS5 跨会话记忆，MEMORY.md + checkpoint.md 持久化 | 论文理解结果、实验历史、失败教训需跨会话保留 |
| **多 Agent 模式** | build / plan / compose 三种模式，按任务切换 | 科研流程需要多角色：实验执行、方案规划、报告撰写 |
| **智能上下文** | checkpoint 机制 + token 预算 + 重要性排序 | 长程科研任务上下文管理：不可全量塞进 prompt |
| **Goal/Stop Judge** | 独立 judge 模型判断任务是否真正完成 | 防止过早声称实验成功或迁移可行 |
| **Dream/Distill** | `/dream` 自动提取经验入 MEMORY；`/distill` 将工作流转为可复用 Skill | 科研经验积累和流程自动化 |

Dream（记忆提取）流程：

```text
用户触发 /dream →
  1. 检查哪些 context 内容是新的（不在已有 MEMORY 中）
  2. 提取重要发现、教训和 insights
  3. 写入 MEMORY.md 供后续会话复用
```

Distill（工作流自动化）流程：

```text
用户触发 /distill →
  1. 回顾当前会话中的完整操作序列
  2. 抽象出可复用的工作流模式
  3. 生成为一个 Skill，下次可直接调用
```

对 AutoAD-Researcher 的启发：

```text
- 每个 run 的实验结果和教训应持久化到类似 MEMORY.md 的跨会话存储
- 设置独立的"科研有效性 judge"，在每次实验后判断结论是否可靠
- 重复流程（跑 baseline、跑对比实验）应 distill 为可复用 skill
- SQLite FTS5 用于全文检索历史实验记录，支持"类似实验是否有人做过"
```

建议吸收优先级：

```text
P1：MEMORY.md 格式的跨会话记忆、独立 judge 模型
P2：Dream/Distill 经验积累、FTS5 全文检索历史实验
```

---

### 4.8 Claude Code 内部实现参考（学习用途）

- 本地路径: `references/coding-agents/claude-code-internals/`
- 内容：原始泄露源码 (TypeScript + Bun) + 社区 Python 重写版 (Claw Code) + Rust 重写版

定位：

仅用于学习 Claude Code CLI 的内部架构设计，不用于产品复制。

学习重点：

```text
- Agent 循环设计：observe → think → act → observe 的闭环机制
- 工具系统架构：Bash、Read、Write、Edit、Agent 等工具的定义与调度
- 权限管理与安全沙箱：分层权限模型 (allow/ask/deny)、命令沙盒
- Hook 系统与 MCP 集成：事件拦截、外部工具接入
- 会话管理与上下文策略：system prompt 分层、上下文压缩、缓存策略
- 子 Agent 调度：Agent 工具的隔离、并发、生命周期管理
```

对 AutoAD-Researcher 的启发：

```text
- 工具调度的"先展示再执行"模式（先生成 patch → 人工确认 → apply）
- Agent 工具的子任务隔离（每个实验在独立 workspace 中执行）
- 安全白名单机制的设计思路
- 系统 prompt 的分层设计：稳定前缀 + 动态后缀
```

⚠️ 使用边界（重要）：

```text
- 仅参考架构思想和交互范式
- 不在报名材料、答辩材料或代码注释中提及"参考泄露源码"
- 不复制具体源码、非公开 prompt、内部协议、特定变量名或文件结构
- 报名材料中统一表述为"参考主流编程 Agent 产品的交互范式和开源 Agent 框架"
```

详细边界说明见下方 [9.3 节](#93-claude-code-泄露源码边界)。

---

## 5. AI Agent 系统提示词参考库 (CL4R1T4S)

> **来源:** [github.com/elder-plinius/CL4R1T4S](https://github.com/elder-plinius/CL4R1T4S)（本地副本位于 `../repos/CL4R1T4S/`）  
> **本地索引:** `references/system-prompts/`  
> **定位:** 全球主要 AI 模型和编程 Agent 的系统提示词、工具定义、行为指南的提取与整理  
> **学习价值:** 理解各 AI 系统的内部指令设计、安全策略、工具架构——对设计 AutoAD 的 Agent 提示词和工具规范有直接参考价值

### 5.1 资源概览

CL4R1T4S 收集了 **60+ 个 AI 产品**的系统提示词，覆盖 **20+ 个厂商**，按用途分为 5 个类别：

```text
references/system-prompts/
├── 01-大厂LLM系统提示词/        # Anthropic Claude (12文件)、OpenAI ChatGPT (12文件)、
│                                  #   xAI Grok (7文件)、Google Gemini (3文件)、
│                                  #   Meta Llama (2文件)、Mistral、Moonshot Kimi、MiniMax
├── 02-编程Agent工具/            # Cursor 2.0、Windsurf、Devin 2.0、Cline、
│                                  #   Replit Agent、Lovable 2.0、Bolt、Vercel v0、SameDev
├── 03-多Agent与自动化/          # Manus (事件流+Planner+Knowledge架构)、Dia、Factory DROID
├── 04-搜索与浏览器/             # Perplexity Deep Research、Brave Leo、MultiOn、Cluely
└── 05-语音与其他/               # Hume Voice AI、Meta Muse Spark
```

### 5.2 核心学习价值

**1. 系统提示词的层次结构：**

各厂商的系统提示词都包含多层结构，对设计 AutoAD 的 Agent 提示词有直接参考：

```text
层次结构（以 Claude Fable 5 为例，1586 行）：
  1. 产品信息与版本标识
  2. 安全策略（版权、有害内容、自伤干预、未成年保护）
  3. 工具定义（JSON Schema：名称、参数、使用场景）
  4. 行为规范（何时用工具、错误处理、输出格式）
  5. 能力边界（知识截止日期、多模态支持、联网限制）
  6. 记忆与持久化（MEMORY.md 系统）
  7. MCP 应用与外部工具集成
  8. Artifacts / Computer Use 等高级功能
```

**2. 编程 Agent 提示词的共性模式：**

```text
所有编程 Agent 的系统提示词都遵循：
  身份定义 → "You are an AI coding assistant..."
  能力声明 → 文件操作、终端、搜索、调试
  工具定义 → 函数签名 + 参数约束 + 使用场景
  行为规范 → 先读后改、最小改动、确认危险操作
  安全边界 → 不生成恶意代码、不泄露提示词
```

**3. 厂商差异反映产品理念：**

| 对比维度 | Anthropic Claude | OpenAI ChatGPT | Google Gemini |
|---|---|---|---|
| 安全策略 | 最详尽（多层分类） | 适中 | 简洁 |
| 工具粒度 | 细粒度定义 | 中等 | 较粗 |
| Agent 能力 | Computer Use + MCP | Codex + Artifacts | Canvas + Python 执行 |
| 语气风格 | 专业、谨慎 | 友好、乐于助人 | 学术、结构化 |

### 5.3 对 AutoAD-Researcher 的启发

**提示词设计：**

```text
- 采用"稳定前缀 + 动态后缀"的分层 prompt 结构（来自 Claude Code 的 cache 策略）
- 每个 Agent 模块（Paper Reader、Transfer Judge、Experiment Planner...）有独立系统提示词
- 工具定义统一采用 JSON Schema 格式，与 DeepSeek API 兼容
- 安全边界明确写入 prompt：禁止删除数据集、禁止修改 eval 脚本等
```

**推荐阅读路径：**

```text
入门：CLAUDE-FABLE-5.md → 最完整的系统提示词结构（1586行）
对比：ChatGPT5 + Gemini 2.5 Pro → 三家大厂的差异
Agent 设计：Manus + Cursor + Devin → 编程 Agent 工具设计模式
趋势：Claude 系列从 3.5 → Fable 5 的版本演进
```

**可直接复用的模式：**

```text
- Claude Code 的工具定义格式 → 参考设计 AutoAD 的工具 schema
- Manus 的事件流 + Planner + Knowledge 架构 → 参考设计 AutoAD 的 pipeline 调度
- Cursor/Devin 的代码修改规范 → 参考设计 Code Agent 的 patch 生成流程
- DeepSeek Reasonix 的 cache-first loop → 参考设计 Model Gateway 的缓存策略
```

### 5.4 本地资源索引

完整文件索引见 `references/system-prompts/_INDEX.md`，关键文件清单见 `references/system-prompts/README.md`。

重点推荐文件：

| 文件 | 内容 | 对 AutoAD 的价值 |
|---|---|---|
| `ANTHROPIC/CLAUDE-FABLE-5.md` | Claude Fable 5 完整系统提示词 (1586行) | Agent 提示词设计的顶级范本 |
| `ANTHROPIC/Claude_Code_03-04-24.md` | Claude Code CLI 系统提示词 | 编程 Agent 的 prompt 设计参考 |
| `CURSOR/Cursor_Prompt.md` + `Cursor_Tools.md` | Cursor 提示词 + 工具定义 | 代码 Agent 工具设计参考 |
| `DEVIN/Devin_2.0.md` | Devin 2.0 AI 软件工程师 | 完整 Agent 能力声明参考 |
| `MANUS/Manus_Prompt.txt` + `Manus_Functions.txt` | Manus 提示词 + 工具定义 | 多 Agent 事件流架构参考 |
| `OPENAI/Codex.md` | OpenAI Codex（含 AGENTS.md 规范） | 项目指令规范参考（类似 CLAUDE.md） |

---

## 6. 模型路由、缓存与成本控制资料

### 6.1 DeepSeek Models & Pricing

- Docs: https://api-docs.deepseek.com/quick_start/pricing

当前公开信息中，DeepSeek V4 包括：

```text
deepseek-v4-flash
deepseek-v4-pro
```

两者支持：

```text
- thinking / non-thinking modes
- JSON Output
- Tool Calls
- 1M context length
- 384K max output
```

建议路由：

| 任务 | 推荐模型 |
|---|---|
| 用户意图澄清 | flash |
| 文献摘要 | flash |
| 论文结构化抽取 | flash |
| 迁移可行性初判 | flash |
| 争议迁移判断 | pro |
| 实验设计 | pro |
| 代码 patch 生成 | pro |
| 日志摘要 | flash |
| 报错初判 | flash |
| 复杂 debug | pro |
| 最终科研结论 | pro |
| 答辩材料 | pro |

### 6.2 DeepSeek Context Caching

- Docs: https://api-docs.deepseek.com/guides/kv_cache

要点：

```text
- Context caching 默认开启
- 命中依赖重叠前缀
- usage 中有 prompt_cache_hit_tokens 和 prompt_cache_miss_tokens
- cache 是 best-effort，不保证 100%
```

建议设计：

```text
stable_prefix:
  - 系统角色
  - 工具 schema
  - 项目协议
  - 异常检测任务定义
  - benchmark 规则
  - 安全边界
  - 输出 schema

dynamic_suffix:
  - 当前用户输入
  - 当前论文片段
  - 当前日志
  - 当前 diff
  - 当前错误栈
```

要避免：

```text
- 每轮重排工具
- 每轮改写 system prompt
- 把完整日志塞进稳定前缀
- 把随机 session id 放入 prompt 前缀
- 每次生成不同格式的 repo summary
```

---

## 7. 异常检测实验底座

### 7.1 Anomalib

- GitHub: https://github.com/open-edge-platform/anomalib
- Docs: https://anomalib.readthedocs.io/

定位：

Anomalib 是视觉异常检测库，收集多种异常检测算法，支持 public/private dataset benchmark 和 custom model development。

对本项目的价值：

```text
- 现成 baseline
- 统一数据接口
- 统一 metrics
- 适合快速 Demo
```

第一版推荐：

```text
baseline:
  - PatchCore
  - PaDiM
  - FastFlow
  - STFPM

dataset:
  - MVTec AD

metric:
  - image AUROC
  - pixel AUROC
  - PRO
  - F1
```

---

### 7.2 MVTec AD

- Official page: https://www.mvtec.com/research-teaching/datasets/mvtec-ad

要点：

```text
- 工业检测异常检测 benchmark
- 5000+ 高分辨率图像
- 15 个 object / texture 类别
- defect-free training images
- test set 含正常与缺陷图
- 提供 pixel-precise annotations
- License: CC BY-NC-SA 4.0，非商业用途限制
```

MVP 推荐类别：

```text
- bottle
- capsule
- cable
```

---

### 7.3 VisA Dataset

- AWS Open Data: https://registry.opendata.aws/visa/
- GitHub reference: https://github.com/amazon-science/spot-diff

要点：

```text
- 12 个类别
- 10,821 张图像
- 9,621 normal
- 1,200 anomaly
- 提供 image-level 和 pixel-level annotation
```

第一版不一定要用。可作为第二个 benchmark。

---

### 7.4 PatchCore

- Paper: https://arxiv.org/abs/2106.08265
- GitHub: https://github.com/amazon-science/patchcore-inspection
- Anomalib implementation: https://github.com/open-edge-platform/anomalib/tree/main/src/anomalib/models/image/patchcore

适合作为第一版 baseline。

可迁移点：

```text
- backbone 替换
- 多尺度特征融合
- memory bank 采样策略
- anomaly score 后处理
- feature normalization
- VFM / CLIP / DINO 特征替换
```

---

### 7.5 PaDiM

- Paper: https://arxiv.org/abs/2011.08785
- Anomalib docs: https://anomalib.readthedocs.io/en/latest/markdown/guides/reference/models/image/padim.html

可作为备用 baseline。

可迁移点：

```text
- patch embedding
- multivariate Gaussian modeling
- 多层特征融合
- anomaly map 生成
```

---

## 8. 工程组件资料

### 8.1 Temporal

- Website: https://temporal.io/
- Docs: https://docs.temporal.io/

用途：

```text
- 长任务编排
- 崩溃恢复
- 重试
- 定时任务
- 状态持久化
```

当前建议：

```text
MVP 不强制使用 Temporal。
先用同步 CLI + SQLite + 文件状态。
如果平台允许长期后台任务，再接 Temporal。
```

### 8.2 Pydantic / JSON Schema

- Pydantic docs: https://pydantic.dev/docs/
- JSON Schema: https://json-schema.org/

用途：

```text
- 管 LLM 输出 schema
- 管实验计划 schema
- 管 metrics schema
- 管 report schema
```

建议所有 Agent 输出都用 schema 约束：

```text
PaperSummary
TransferReport
ExperimentPlan
PatchPlan
RunResult
ValidityReport
FinalReport
```

### 8.3 OpenTelemetry

- Docs: https://opentelemetry.io/docs/

用途：

```text
- traces
- metrics
- logs
- request-level debugging
```

当前建议：

```text
MVP 先用 JSONL 日志。
后续再接 OpenTelemetry。
```

### 8.4 MLflow

- Website: https://mlflow.org/
- GitHub: https://github.com/mlflow/mlflow

用途：

```text
- 实验 tracking
- 参数、指标、artifact 记录
- 模型和报告版本化
```

当前建议：

```text
MVP 可不用 MLflow。
用 runs/{run_id}/ 文件夹已经足够。
如果后续实验变多，再接 MLflow。
```

---

## 9. 科研有效性与安全边界

### 9.1 Scientific Validity Supervisor

必须内置，不一定是独立 Agent，可以是一个普通函数 + LLM checker。

检查项：

```text
- 是否使用 test label
- 是否改变 evaluation script
- 是否改变 dataset split
- 是否把异常样本混入训练
- 是否偷看 ground truth mask
- 是否覆盖 baseline 结果
- 是否只保留成功实验
- 是否只在单 seed 上偶然提升
- 是否改变评价指标口径
```

输出：

```json
{
  "validity": "pass | warning | fail",
  "issues": [],
  "required_fixes": [],
  "can_report_as_improvement": true
}
```

### 9.2 执行安全边界

允许：

```text
- 读取项目目录
- 修改指定实验目录
- 运行指定 Python 脚本
- 读取日志
- 生成报告
```

禁止：

```text
- 删除数据集
- 删除整个项目
- 访问系统敏感路径
- 上传代码到外部
- 安装未知包
- 执行 rm -rf 类危险命令
- 覆盖 baseline 结果
```

建议实现：

```text
- subprocess 白名单
- Docker/conda 沙盒
- 每次修改生成 patch，不直接覆盖
- 人工确认后才 apply patch
- 运行前展示 command
- 所有 stdout/stderr 保存
```

### 9.3 Claude Code 泄露源码边界

不要把泄露源码纳入资料库，不要在报名材料、答辩材料或代码注释中提到“参考泄露源码”。

可参考：

```text
- 公开产品交互
- diff 展示方式
- 任务计划格式
- 工具调用节奏
- 用户确认节点
- 错误恢复体验
```

不可参考：

```text
- 具体源码
- 非公开 prompt
- 内部协议
- 特定变量名
- 特定文件结构
- 无法说明来源的代码片段
```

报名材料推荐表述：

> 参考主流编程 Agent 产品的交互范式和开源 Agent 框架，设计面向科研实验的可控代码执行闭环。

---

## 10. 文档解析工具

论文解析（Paper Reader）是整个闭环的第一步，需要高精度地从 PDF 中提取结构化信息。以下两个工具是当前开源领域最强选择。

### 10.1 MinerU（高精度 PDF 解析）

- GitHub: https://github.com/opendatalab/MinerU
- 项目页: https://mineru.net/
- 本地路径: `repos/MinerU/`

定位：

MinerU 是上海人工智能实验室 OpenDataLab 团队开源的高精度文档解析引擎，专注复杂 PDF → Markdown/JSON。核心技术指标：

```text
- 布局检测：doclayout_yolo（支持单栏/双栏/混合版面）
- 公式识别：UniMERNet 自研模型 → LaTeX
- 表格识别：StructTable-InternVL2-1B → HTML，支持跨页合并
- OCR：PaddleOCR，109 种语言
- 三引擎架构：pipeline（传统 CV 模型）/ vlm-engine（VLM 高精度）/ hybrid-engine（混合）
```

对本项目的价值：

```text
- 论文 PDF → 结构化 Markdown：提取标题、段落、公式、表格
- 公式 → LaTeX：用于迁移判断（理解方法涉及什么数学组件）
- 表格 → HTML：提取实验结果表格做 baseline 对比
- 布局分析：区分正文、图表、公式、页眉页脚
- 多语言 OCR：支持中英文论文混合解析
```

MVP 建议用法：

```text
输入：论文 PDF
流程：MinerU 解析 → Markdown → 喂给 Paper Reader Agent
输出：结构化论文理解（核心方法、模块、数据假设、指标）
```

属于 P0 组件：论文理解质量直接决定下游迁移判断的准确性。

### 10.2 MarkItDown（轻量多格式转换）

- GitHub: https://github.com/microsoft/markitdown
- 本地路径: `repos/markitdown/`

定位：

MarkItDown 是微软 AutoGen 团队开源的轻量级文件→Markdown 转换工具。与 MinerU 互补：

| 对比维度 | MinerU | MarkItDown |
|---|---|---|
| PDF 精度 | SOTA（公式/表格/布局） | 一般 |
| 格式覆盖 | PDF + Office | PDF + Office + 图片 + 音频 + HTML + ZIP + YouTube + EPUB |
| 体积 | 重（需 GPU，多模型依赖） | 极轻（核心几百行，按需安装） |
| 许可证 | 自定义（基于 Apache 2.0） | MIT |
| MCP Server | 支持 | 支持（STDIO / HTTP / SSE） |

对本项目的价值：

```text
- 快速处理非 PDF 格式输入：PPT/Word 论文幻灯片、Excel 数据表、HTML 网页
- MCP Server 集成：可直接对接到 Claude Desktop / Cursor 等 AI 工具
- 插件系统：可自定义异常检测论文专用的 Converter（例如提取特定表格格式）
- 低资源场景：不需要 GPU，适合 MVP 阶段快速原型
- 流式处理：v0.1.0 起不写临时文件，全内存操作
```

建议角色分工：

```text
MinerU → 主要论文解析引擎（高精度 PDF 场景）
MarkItDown → 辅助格式转换 + MCP 集成 + 低资源备选方案
```

### 10.3 与现有仓库的对应关系

本地目录结构：

```text
repos/
├── MinerU/         ← 高精度 PDF 解析（公式、表格、布局）
├── markitdown/     ← 轻量多格式→Markdown
├── anomalib/       ← 异常检测 baseline
├── patchcore-inspection/ ← PatchCore baseline
└── ...
```

---

## 11. 资料优先级

### P0：必须读

```text
1. AutoLab
2. AutoSOTA
3. Toward Autonomous Long-Horizon Engineering for ML Research
4. Claw AI Lab
5. karpathy/autoresearch
6. Anomalib
7. MVTec AD
8. MinerU（论文解析核心引擎）
9. DeepSeek Pricing + Context Caching
10. CL4R1T4S Claude Fable 5 系统提示词（Agent 设计顶级范本）
```

### P1：重要但不影响报名

```text
1. AutoScientists
2. MiMoCode（持久化记忆 + judge 模型 + Dream/Distill）
3. Claude Code 内部实现参考（Agent 循环 + 工具系统 + 权限模型）
4. OpenHands SDK
5. SWE-agent
6. mini-SWE-agent
7. Aider
8. MarkItDown（辅助格式转换 + MCP）
9. CL4R1T4S 完整系统提示词库（Agent 提示词设计参考）
10. Reasonix
11. PaperBench
12. CORE-Bench
```

### P2：后期加分

```text
1. AutoFigure
2. Agent Laboratory
3. The AI Scientist
4. MLAgentBench
5. OpenCode
6. MiMoCode Dream/Distill 深入集成
7. CL4R1T4S 全量系统提示词比对研究
8. VisA Dataset
9. MLflow / OpenTelemetry / Temporal
```

---

## 12. 推荐最终技术路线表述

可以在报名材料中使用：

> 本项目面向异常检测科研流程中“论文理解—方法迁移—实验验证—结果反思”的高频痛点，构建一个人机协同的科研智能体系统。系统不依赖复杂黑盒 Agent 框架，而是采用经典软件工程架构：将大模型封装为可替换组件，用数据库和文件工作区管理长期状态，用类型系统和 JSON Schema 约束模型输出，用沙盒、白名单和人工确认控制代码执行风险，用日志、trace 和评测集持续衡量系统质量。  
>
> 系统参考 AutoLab、AutoSOTA、AiScientist、Claw AI Lab 等长周期科研智能体工作，借鉴 MiMoCode 的持久化记忆和 judge 模型设计，参考 Claude Code 等主流编程 Agent 的交互范式和工具系统架构，基于 CL4R1T4S 系统提示词库进行 Agent 提示词工程，将科研流程拆解为意图澄清、论文解析、迁移判断、实验规划、代码修改、实验执行、日志分析、有效性监督和结果反思等模块。第一版聚焦视觉异常检测，基于 Anomalib、MVTec AD 和 PatchCore 等开源实验底座，实现从论文输入到最小实验结论输出的 Dry Lab 闭环。

---

## 13. BibTeX 汇总

```bibtex
@misc{xu2026autolabfrontiermodelssolve,
      title={AutoLab: Can Frontier Models Solve Long-Horizon Auto Research and Engineering Tasks?}, 
      author={Zhangchen Xu and Junda Chen and Yue Huang and Dongfu Jiang and Jiefeng Chen and Hang Hua and Zijian Wu and Zheyuan Liu and Zexue He and Lichi Li and Shizhe Diao and Jiaxin Pei and Jinsung Yoon and Hao Zhang and Mengdi Wang and Radha Poovendran and Misha Sra and Alex Pentland and Zichen Chen},
      year={2026},
      eprint={2606.05080},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2606.05080}, 
}

@misc{gao2026autoscientistsselforganizingagentteams,
      title={AutoScientists: Self-Organizing Agent Teams for Long-Running Scientific Experimentation}, 
      author={Shanghua Gao and Ada Fang and Marinka Zitnik},
      year={2026},
      eprint={2605.28655},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2605.28655}, 
}

@misc{chen2026autonomouslonghorizonengineeringml,
      title={Toward Autonomous Long-Horizon Engineering for ML Research}, 
      author={Guoxin Chen and Jie Chen and Lei Chen and Jiale Zhao and Fanzhe Meng and Wayne Xin Zhao and Ruihua Song and Cheng Chen and Ji-Rong Wen and Kai Jia},
      year={2026},
      eprint={2604.13018},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2604.13018}, 
}

@misc{li2026autosotaendtoendautomatedresearch,
      title={AutoSOTA: An End-to-End Automated Research System for State-of-the-Art AI Model Discovery}, 
      author={Yu Li and Chenyang Shao and Xinyang Liu and Ruotong Zhao and Peijie Liu and Hongyuan Su and Zhibin Chen and Qinglong Yang and Anjie Xu and Yi Fang and Qingbin Zeng and Tianxing Li and Jingbo Xu and Fengli Xu and Yong Li and Tie-Yan Liu},
      year={2026},
      eprint={2604.05550},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2604.05550}, 
}

@misc{wu2026clawailabautonomous,
      title={Claw AI Lab: An Autonomous Multi-Agent Research Team}, 
      author={Fan Wu and Cheng Chen and Zhenshan Tan and Taiyu Zhang and Xinzhen Xu and Yanyu Qian and Dingcheng Gao and Lanyun Zhu and Qi Zhu and Yi Tan and Deyi Ji and Guosheng Lin and Tianrun Chen and Deheng Ye and Fayao Liu},
      year={2026},
      eprint={2605.22662},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2605.22662}, 
}

@misc{zhu2026autofiguregeneratingrefiningpublicationready,
      title={AutoFigure: Generating and Refining Publication-Ready Scientific Illustrations}, 
      author={Minjun Zhu and Zhen Lin and Yixuan Weng and Panzhong Lu and Qiujie Xie and Yifan Wei and Sifan Liu and Qiyao Sun and Yue Zhang},
      year={2026},
      eprint={2602.03828},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2602.03828}, 
}

@software{mineru2025,
      title={MinerU: A High-Precision Document Parsing Tool for Complex PDFs and Office Documents},
      author={OpenDataLab, Shanghai AI Laboratory},
      year={2025},
      url={https://github.com/opendatalab/MinerU},
      note={Open-source document extraction engine; pipeline + VLM + hybrid backends; formulas→LaTeX, tables→HTML, 109-language OCR. Local path: repos/MinerU/},
}

@software{markitdown2025,
      title={MarkItDown: A Lightweight Utility for Converting Various Files to Markdown},
      author={AutoGen Team, Microsoft},
      year={2025},
      url={https://github.com/microsoft/markitdown},
      note={MIT-licensed; supports PDF, Office, images, audio, ZIP, EPUB, YouTube URLs; plugin system; MCP server. Local path: repos/markitdown/},
}
```

---

## 14. 下一步建议

短期只做三件事：

```text
1. 把资料库变成 GitHub README 或 Notion 页面。
2. 选 1 个 baseline：PatchCore。
3. 选 1 个 dataset：MVTec AD 的 bottle 类别。
```

报名材料里不要铺开所有资料。真正要讲清：

```text
AutoSOTA / AutoLab 给出科研闭环范式；
AiScientist/File-as-Bus 给出状态管理方法；
Claw AI Lab 给出可交互、可检查、可回滚的实验室形态；
Anomalib + MVTec AD + PatchCore 给出可落地的异常检测实验底座；
DeepSeek / Reasonix 给出成本可控的模型路由和缓存思路。
```
