# AutoAD 实验 Agents 设计包 — 开发计划审核报告

> 审核对象：`notes/后端开发/AutoAD_实验Agents设计包/`（8 份文档）  
> 审核日期：2026-07-16  
> 审核维度：可行性、完整性、风险可控性

---

## 整体结论

**核心风险等级：高**

这组文档在**架构设计深度和系统完备性上表现出色**，但在**开发落地层面存在严重缺口**，核心问题集中于：

1. **「设计」与「计划」严重脱节**：文档标题为「开发计划」，但全部文档中**没有任何可落地的排期、人力配置、资源清单、里程碑或交付物标准**——本质上这是一份**架构设计文档**，而非可执行的开发计划。
2. **文档内部一致性有瑕疵**：至少 3 处核心设计矛盾（activation evidence 模型、Coordinator 持久化方案、有效性枚举），开发人员按不同文档实现将产生冲突。
3. **LLM 认知成本黑盒**：没有对每实验轮次认知调用量的任何估算，CognitiveBudget 阈值无法配置，存在 API 成本失控风险。
4. **安全合规空白**：在涉及代码修改、文件系统访问、GPU 调度的系统中，安全方案完全缺失。

**主要待完善方向**：补全排期与资源计划 → 统一文档间的设计矛盾 → 补充认知成本估算 → 增加安全合规方案 → 完善依赖关系与关键路径分析。

---

## 高优先级问题

### H1. 排期与人力配置完全缺失

| 维度 | 内容 |
|------|------|
| **问题描述** | 全部文档无任何排期计划、里程碑日期、资源投入时间表、人力配置（角色/人数/工时）。01~07 的每个 PR 都有编号但无时间估算。PR-01A~D、PR-02A~E、PR-03A~E、PR-04A~E、PR-05A~E、PR-06A~F 均只有功能描述，没有「何时由谁完成」。 |
| **潜在风险** | 无法判断项目总周期，无法进行资源规划与进度追踪，管理层无法做投入决策，计划整体不可落地。 |
| **需补充** | 每 PR 的估算工时、对应负责人角色、各阶段里程碑节点与交付物验收标准、总体时间线。 |

### H2. 无正式任务依赖关系矩阵与关键路径

| 维度 | 内容 |
|------|------|
| **问题描述** | `00_README` 的「推荐开发顺序」为纯文本箭头图（PR-001A → 计划01 → 计划04 的 Job 基础 → 计划03 → ...），未标注 PR 间的交叉依赖细节。例如：PR-04A (Job/Worker) 是否依赖 PR-01A 的 Session 完成？PR-04B (GpuAllocator) 是否依赖 PR-04A？PR-03C (ExecutorAgent) 是否需要 PR-02A (Idea Tree) 的 InterventionContract 先完成？均未说明。无关键路径分析。 |
| **潜在风险** | 任务并行度无法判断，资源调度混乱，开发顺序冲突导致阻塞。 |
| **需补充** | 完整的任务依赖 DAG（有向无环图），标注关键路径与最长链长度，标注可并行窗口。 |

### H3. 文档间至少存在 3 处核心矛盾

#### H3.1 activation evidence 模型三处不一致

| 文档位置 | 原文 |
|----------|------|
| `01_实验Agents大框架.md`「第一版开发边界」 | 要求「implementation activation evidence」 |
| `04_ExecutorAgent与代码修改闭环.md` 第 3.6 节 | 「第一版不引入 activation evidence verification」 |
| `06_实验有效性_Reflection与决策.md` 第 3.2 节 | 「去掉 VERIFIED/UNVERIFIED/INVALID 三级枚举，改为四个独立布尔 check」 |

- **矛盾点**：第一版到底要不要实现 activation evidence？如果实现，用三级枚举还是四个布尔 check？
- **风险**：开发人员按不同文档实现，导致代码不统一，返工。

#### H3.2 Coordinator 持久化方案矛盾

| 文档位置 | 原文 |
|----------|------|
| `01_实验Agents大框架.md` 第 4.5 节 | DeepAgents checkpoint 是「可丢失」的缓存，「可以丢失」 |
| `03_ResearchCoordinator与IdeaTree.md` 第 3.3 节 | Coordinator 配置要求「Persistent/checkpoint」 |

- **矛盾点**：如果 checkpoint 「可以丢失」，那 Coordinator 的持久化到底依赖什么机制？「可丢失」和「Persistent」字面矛盾。
- **风险**：Coordinator 持久化实现方案选择错误，崩溃恢复机制不可靠。

#### H3.3 激活验证命名体系冲突

| 文档位置 | 原文 |
|----------|------|
| `06_实验有效性_Reflection与决策.md` 第 3.2 节 | 「不称为 ACTIVATION_VERIFIED。不宣称『代码已按假设生效』」 |
| `04_ExecutorAgent与代码修改闭环.md` 第 6.4 节 | 仍然使用 VERIFIED/UNVERIFIED/INVALID 描述激活验证测试 |

- **矛盾点**：一份文档明确放弃的三级枚举，在另一份文档的测试方案中继续使用。
- **风险**：测试验收标准与实现定义不一致。

- **整体需补充**：全文档统一有效性模型定义；明确 Coordinator 持久化的技术实现方案（是 DeepAgents 框架自身能力还是自定义外部状态持久化）。

### H4. LLM Agent 认知成本缺乏总量估算

| 维度 | 内容 |
|------|------|
| **问题描述** | `01_实验Agents大框架.md` 提出了 CognitiveBudget 的四项硬限制（call_count, total_cost, step_count, wall_seconds），且在每个 AgentTaskSpec 中有 token_budget/wall_time_budget_sec。但**全文档没有任何关于每轮实验实际 LLM 调用次数、token 消耗量、成本的数据估算**。参考项目（AutoSOTA、AI-Scientist、Arbor、mini-swe-agent）的实际消耗数据也未给出。导致 `max_calls`、`max_tokens`、`max_cost` 等阈值无法在代码中设定——设太小频繁打断，设太大成本失控。 |
| **潜在风险** | 上线后 LLM API 账单远超预期，或 CognitiveBudget 频繁触发打断正常实验流。 |
| **需补充** | 基于参考项目经验的典型认知成本估算表（Compact Cycle 每轮约 X tokens、Exploratory Cycle 约 Y tokens、sub-agent 调用平均 Z tokens），作为 CognitiveBudget 默认阈值的设置依据。 |

### H5. 硬件/GPU 资源需求清单完全缺失

| 维度 | 内容 |
|------|------|
| **问题描述** | 未明确开发、测试、运行三阶段的 GPU 型号/数量、存储空间、RAM、网络需求。`04_ExperimentJob` 的 ResourceLease 本地实现假设单机多卡，但未说明开发调试需要什么配置的机器、集成测试是否需要多机、AD-AgentBench 是否需要真实 GPU。`02_ExperimentSession` 的 GPU compute probe 仅描述了运行时的探测逻辑，未说明开发阶段的 GPU 资源要求。 |
| **潜在风险** | 部署/测试时发现可用资源不足，需临时申请/采购，造成延期。 |
| **需补充** | 三阶段资源需求清单：开发机最低配置、CI 测试机 GPU 需求、运行生产环境的 GPU 规格/数量/显存需求。 |

### H6. 无安全合规方案

| 维度 | 内容 |
|------|------|
| **问题描述** | 仅 `02_ExperimentSession` 第 3.4 节一处提到「对 secret 做脱敏」但无具体方案。完整缺失：代码仓库 SSH 密钥管理、第三方 LLM API key 存储与轮换机制、用户数据隐私保护、日志中的敏感信息脱敏规范、容器安全策略、网络安全策略（训练进程是否需要外网访问、API 端点的认证授权）。 |
| **潜在风险** | API key 泄露导致经济损失；用户代码仓库凭据泄露；系统被滥用执行恶意命令。 |
| **需补充** | 统一安全设计方案：密钥管理机制（环境变量/vault）、日志脱敏规范（正则规则列表）、网络策略（出口白名单、API 认证）、容器安全（非 root 运行、只读文件系统）。 |

---

## 中优先级问题

### M1. 现有 Pipeline 子系统复用边界未定义

| 维度 | 内容 |
|------|------|
| **问题描述** | `02_ExperimentSession` 第 3.3 节提到创建 `experiment_environment_prepare` PipelineJob，但整套文档未描述现有 Pipeline 子系统的架构、Job Store 的实现、Worker dispatch 机制、重试策略。读者不清楚哪些能力可直接复用、哪些需要改造。 |
| **潜在风险** | 开发时发现现有 Pipeline 能力不足以支撑新需求（如 Job 状态扩展、长时间 Job 的 heartbeat），需要额外改造，导致排期低估。 |
| **需补充** | 现有 Pipeline 子系统的能力评估与接口分析，明确复用层与改造层。 |

### M2. PR 拆分粒度不一致

| 维度 | 内容 |
|------|------|
| **问题描述** | PR-02A（Idea Tree）包含 schema/store/mutation/revision/event/recovery/immutable commit 七个子任务，粒度偏大，单个 PR 估算工时可能在 2~4 周以上。PR-06E（AD-AgentBench）要求建立 fixturerepo + 10 个 case 的 fixture + fake GPU + replay harness，也是一个大型 PR。其他 PR（如 PR-01A~D、PR-05A~E）粒度相对合理。 |
| **潜在风险** | 大 PR 估算不准、code review 困难、合并时冲突概率高、集成风险集中暴露。 |
| **需补充** | 对 02A、06E 等大 PR 进一步合理拆解（如将 Idea Tree 拆为 schema/store → mutation/event → recovery 三个子 PR）。 |

### M3. 人力角色与技能矩阵空白

| 维度 | 内容 |
|------|------|
| **问题描述** | 设计包涉及多个技术栈组件：DeepAgents 框架定制、SEARCH/REPLACE 四层策略栈集成、GPU 资源管理与调度、训练 Sentinel 与 heartbeat 机制、Anomalib/PatchCore 领域适配器。但完全没有指定所需角色（后端工程师？ML 工程？AI infra？）、技能要求、人数、投入时间。无负责人分配。 |
| **潜在风险** | 人员能力与岗位不匹配导致开发效率低下，关键组件无人负责。 |
| **需补充** | 角色定义（如 DeepAgents 框架工程师 ×1、后端/Pipeline 工程师 ×1、ML infra 工程师 ×1）、技能矩阵（Python、PyTorch、异步编程、GPU 编程）、具体负责人。 |

### M4. Champion B_test 通过标准未定义

| 维度 | 内容 |
|------|------|
| **问题描述** | `06_实验有效性_Reflection与决策.md` 第 7 节 DecisionEngine 规则说「B_test gate passes → champion」，但未定义什么是「pass」：与 baseline 比较的 p-value 阈值是多少（0.05? 0.01?）？是否需要效应量（Cohen's d）最小可检测标准？是 metric-specific 阈值还是全局统一？需要多少个 seed 通过？B_test 与 B_dev 方向不一致时如何处理？ |
| **潜在风险** | B_test gate 实现时无标准可依，开发人员自行选择阈值，可能导致统计不可靠或 false promotion。 |
| **需补充** | B_test gate 的完整统计决策逻辑（假设检验方法、效应量阈值、seed 数要求、冲突处理）。 |

### M5. ResourceLease expires_at 回收策略不完整

| 维度 | 内容 |
|------|------|
| **问题描述** | `04_ExperimentJob` 第 3.1 节 Schema 包含 `expires_at` 字段，但未说明：expires_at 的合理值如何设定（基于训练时长估算？attempt 超时的 1.5x？）；过期后的具体回收流程——是由 Worker 心跳检测？还是独立 reaper 进程？过期后正在运行的训练进程是立即 SIGKILL 还是先 SIGTERM 等待 graceful？回收后 lease 状态如何转换（RELEASED / LOST / EXPIRED）？ |
| **潜在风险** | GPU 资源死锁（lease 过期未回收）或训练被意外瞬杀导致 checkpoint 未保存。 |
| **需补充** | lease 过期策略的完整设计：overhead factor 设定规则、pre-expiry 警告窗口、graceful shutdown 流程（SIGTERM → 等待 → SIGKILL）、过期后状态机、回收执行者（Worker vs 独立 Sentry）。 |

### M6. 环境 Probe timeout 值未定义

| 维度 | 内容 |
|------|------|
| **问题描述** | `02_ExperimentSession` 第 3.4 节对所有探测命令要求 shell=False 和 timeout，但未给每个 probe 命令的具体 timeout 值。不同命令时延差异极大：`nvidia-smi` 约 0.5s，`uv sync` 可能需要 300s+，`torch import` 约 5~15s。统一 timeout 值会导致太短误判失败或太长阻塞。 |
| **潜在风险** | timeout 设置不当导致环境准备阶段误判（探测定时失败→错误重试→浪费大量时间）。 |
| **需补充** | 为每个 probe 命令指定合理的 timeout 范围，并在设计上区分「快 probe」和「慢 install」的超时策略。 |

---

## 低优先级问题

### L1. PR/文档编号引用不一致

| 维度 | 内容 |
|------|------|
| **问题描述** | `00_README.md` 引用「开发计划 01~06」对应实际文件名 `02~07`（00 是 README，01 是大框架，02~07 是计划 01~06）。PR 编号混用格式：`00_README` 写「PR-001A」，`02_ExperimentSession.md` 第 3 节也用「PR-001A」，但其他文档 PR 编号为「PR-01A~D」风格。同一 PR 两种编号。 |
| **潜在风险** | 内部沟通混淆，任务追踪系统（Jira/GitHub Issues）编号不统一。 |
| **需补充** | 统一 PR 编号规范（建议统一为 PR-01A 风格），统一文档引用方式。 |

### L2. Event 系统无统一 schema

| 维度 | 内容 |
|------|------|
| **问题描述** | `01_大框架` Artifact 目录有 `events.jsonl`，`02_ExperimentSession` 第 5 节写 Event，`04_ExperimentJob` 第 2 节要求 event，`05_实验有效性` 多处提到 EventStore。但无一文档给出 Event 通用 schema 定义（event_id, event_type, timestamp, source_component, payload, trace_id, session_id, severity）。 |
| **潜在风险** | 各组件各自实现事件格式，导致日志消费和调试困难，可观测性打折扣。 |
| **需补充** | 统一的事件 schema 定义（共用文件 `events/schema.py`），建议包含 event_id/uuid、event_type、source、timestamp、payload_json、trace_id、severity。 |

### L3. Artifact 日志文件的写入策略未定义

| 维度 | 内容 |
|------|------|
| **问题描述** | `01_大框架` 第 14 节 Artifact 目录包含 `trajectory.jsonl` 和 `events.jsonl`，但未说明：由哪个组件写入（Coordinator？Worker？全局 Logger？），写入时机（实时追加 vs 阶段写入），写入频率，文件 rolling 策略与 retention policy，文件锁或并发写入冲突处理。 |
| **潜在风险** | 文件写入冲突（多进程同时写同一 jsonl）导致数据损坏或丢失；磁盘无限增长。 |
| **需补充** | 日志文件写入策略（append-only with flock，按 session 切分，最大保留 N GB/G 天）。 |

### L4. AD-AgentBench 范围与验收标准不明确

| 维度 | 内容 |
|------|------|
| **问题描述** | `07_收敛_认知预算与端到端验收.md` 第 9 节 PR-06E 要求建立 AD-AgentBench fixture，包含 10 个 case 各一个 fixture repo。但未定义：fixture 的数据集规模（CIFAR-10？MVTec？自定义 100 条样本？），每个 case 的训练时长预期（秒级？分钟级？），是否需要真实 GPU（fake GPU 的 mock 程度要求？），定量验收指标（case pass 率？端到端成功率？）。 |
| **潜在风险** | benchmark 开发范围随意放大（如选了大数据集或真实训练），消耗过多资源和时间。 |
| **需补充** | AD-AgentBench 的结构定义、每个 case 的 repo 大小和数据量约束、验收 metric（pass/fail 判定标准）、GPU 需求分级（必需/可选/fake）。 |

---

## 问题统计

| 优先级 | 数量 | 关键编号 |
|--------|------|----------|
| 高 | 6 | H1 ~ H6 |
| 中 | 6 | M1 ~ M6 |
| 低 | 4 | L1 ~ L4 |
| **合计** | **16** | |

---

## 附件：审核范围

| 文档 | 文件名 | 行数 |
|------|--------|------|
| 文档索引与开发顺序 | `00_README_文档索引.md` | 80 |
| 实验 Agents 大框架 | `01_实验Agents大框架.md` | 793 |
| 开发计划 01：Session 与环境 | `02_ExperimentSession与环境准备.md` | 338 |
| 开发计划 02：Coordinator & Idea Tree | `03_ResearchCoordinator与IdeaTree.md` | 361 |
| 开发计划 03：Executor & 代码修改 | `04_ExecutorAgent与代码修改闭环.md` | 352 |
| 开发计划 04：Job & GPU & 监控 | `05_ExperimentJob_GPU资源与训练监控.md` | 401 |
| 开发计划 05：有效性 & Reflection | `06_实验有效性_Reflection与决策.md` | 455 |
| 开发计划 06：收敛 & 端到端验收 | `07_收敛_认知预算与端到端验收.md` | 379 |
