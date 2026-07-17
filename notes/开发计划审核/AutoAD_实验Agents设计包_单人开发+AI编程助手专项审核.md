# AutoAD 实验 Agents 设计包 — 单人开发 + AI 编程助手专项技术审核报告

> 审核对象：`notes/后端开发/AutoAD_实验Agents设计包/`（8 份文档）  
> 审核日期：2026-07-16  
> 审核视角：单人开发 + 重度依赖 AI 编程助手的 Agent 系统技术风控

---

## 整体技术风险评级：**极高**

本系统在架构层面设计完备，但**技术实现深度与单人开发能力之间存在巨大鸿沟**。开发任务是完整的通用科研 Agent 框架（7 个 Agent 角色、6 个子系统、20+ 组件），但开发资源配置仅为 1 人 + AI 辅助，且重度依赖一个尚未开源的内部框架 `DeepAgents`。核心风险并非来自单点技术难点，而是**系统复杂度远超单人掌控极限**与**AI 生成代码的不可靠性将随系统规模指数级放大**。

**核心改进方向**：大幅裁剪第一版范围至「一个可跑通的最小闭环」而非「完整框架」；为 `DeepAgents` 框架的不可用准备备选方案；建立单人适用的代码质量管控与架构一致性校验机制；量化认知成本并设置硬性 API 预算红线。

---

## 高优先级风险

### H1. 系统规模与单人开发能力严重不匹配

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档范畴。系统共定义 7 个 Agent 角色（Coordinator、IdeaExplorer、Reviewer、Executor、Reflection、HealthDiagnosis、StrategyDiagnostic）、6 个子系统（Session、Environment、Job/GPU、Validity、Convergence、Strategy）、20+ 组件、30+ 个 PR。对照 `01_大框架`「第一版必须具备」列表（15 项）和「推荐开发顺序」中的 9 个阶段。 |
| **潜在风险** | 单人开发需同时掌握：Python 后端（async/await、subprocess/Popen 管理）、GPU 资源调度（nvidia-smi、lease 管理、进程组）、Git worktree 操作、DeepAgents 框架（未开源）、SEARCH/REPLACE 四层策略栈（aider 源码 757 行）、多领域 Adapter（anomalib、PatchCore）、AI Agent prompt 工程。一个开发者几乎不可能同时精通上述所有领域。AI 编程助手可以弥补编码速度，但无法替代开发者对**跨模块架构一致性**的判断——这是单人开发的核心瓶颈。 |
| **需核对/补充** | ① 第一版必须大幅裁剪：建议将「两轮真实迭代」目标收缩为「单轮实验可闭环」；② 明确开发者对 DeepAgents 框架的熟悉程度（是否参与过该框架开发）；③ 评估 AI 编程助手在 GPU 调试、git worktree 操作等系统编程场景的实际辅助效率。 |

### H2. DeepAgents 框架是最大的技术依赖黑盒

| 维度 | 内容 |
|------|------|
| **问题定位** | `01_大框架` 全文依赖 `create_deep_agent()` 和 `CognitiveTaskRunner` 抽象。`03_Coordinator` 也说 Coordinator 是「持久运行的 DeepAgents Agent」。但 DeepAgents 框架本身**闭源/内部未发布**：文档标注的参考来源 `/root/autodl-tmp/repos/` 中的 DeepAgents 项目（来自 OpenCode）仅有 prompt 层实现，无 Agent 框架核心。 |
| **潜在风险** | ① DeepAgents 框架若未稳定，所有 Agent 创建、checkpoint、summarization middleware 等核心依赖将成为空壳；② 框架 API 变更将导致大规模返工；③ 文档宣称的「CognitiveTaskRunner 隔离层」只有 Protocol 定义而无任何实现或 mock——在框架不可用的情况下，整个系统无法启动。AI 编程助手无法帮助发明一个不存在的框架能力。 |
| **需核对/补充** | ① DeepAgents 框架当前状态（可用版本？API 文档？）；② 若框架不可用，是否已有 B 方案（如直接使用 LangGraph 或 AutoGen 实现 Coordinator）；③ 建议在框架尚未稳定之前，将 CognitiveTaskRunner 的实现作为第一版 PR 之一，并准备 MockTaskRunner 支持离线开发。 |

### H3. AI Agent 效果不确定性被严重低估，无调优缓冲机制

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档。所有 Agent 的行为设计均假设 LLM 能「一次按 structured output schema 正确输出」。例如 `03_Coordinator` 第 3.4 节 Compact Cycle 要求「一次 LLM 返回 CycleDecision」，以及所有 Agent 的 output schema 强依赖模型输出格式。未讨论：schema 解析失败时的重试策略（retry with different prompt？回退到 free-form？）、模型 response 中 hallucinated evidence ref 的校验、CycleDecision 中下一个 action 不可执行时的降级方案。 |
| **潜在风险** | AI Agent 系统开发的最大已知坑点是：**模型在简单 demo 中表现优秀，但在真实长尾场景中频繁输出无效 JSON、幻觉引用不存在的 attempt/evidence、选择超出边界的 action**。文档将大量核心决策「托付」给 LLM 的结构化输出，但没有为这些输出不可靠的情况设计兜底逻辑。单人开发者将花费大量时间在「修 prompt → 调 schema → 发现新失败模式」的循环中，远超出文档预估的开发周期。 |
| **需核对/补充** | ① 为每个 Agent 的输出增加 schema 解析容错（解析失败 → 自动重试 N 次 + 降级方案）；② 所有 evidence_ref 应做存在性校验（尝试引用不存在的 ID → 拒绝并提示）；③ 为 Coordinator 的 CycleDecision 添加 action 合法性校验；④ 文档中增加对 Agent 效果调试周期的估算（建议按开发周期 2x 预留）。 |

### H4. 2+ 个未开源的外部依赖构成严重断供风险

| 维度 | 内容 |
|------|------|
| **问题定位** | 文档依赖的外部组件中，至少 2 个无法直接获取：① **DeepAgents 框架**（**核心依赖**，见 H2）；② **aider 的 SEARCH/REPLACE 策略栈**（`04_ExecutorAgent` 第 3.3 节「直接复用 aider 的 `flexible_search_and_replace()`」——但 aider 的 AGPL 许可证存在商用合规风险，且仓库是否可稳定引入依赖）。 |
| **潜在风险** | ① DeepAgents 框架不可用 → 整个 Agent 系统无运行基础；② aider 的 AGPL 许可证与项目可能不兼容，且其 `flexible_search_and_replace()` 内部依赖 aider 的完整代码结构，独立提取难度大。单人开发者无法同时处理许可证合规问题和代码提取工作。 |
| **需核对/补充** | ① 确认 DeepAgents 框架的开源计划/内部可用性，制定「框架不可用」的 B 方案；② 评估 aider AGPL 许可证兼容性，若不兼容，准备独立的 SEARCH/REPLACE 实现（参考 SWE-agent 或 OpenCode 的纯 MIT 实现）；③ 建议所有外部依赖锁定版本号并 vendor。 |

### H5. 认知成本（LLM Token 消耗）黑盒 —— 单人无法承受的 API 账单风险

| 维度 | 内容 |
|------|------|
| **问题定位** | `01_大框架` 第 13 节 CognitiveBudget 列出 max_calls、max_cost、max_tokens 等限制，`05_ExperimentJob` 第 5.6 节 BatchSupervisor 和 `06_实验有效性` 第 6 节 Reflection 也涉及 LLM 调用。但全文档无任何对单轮实验 Token 消耗量的估算。 |
| **潜在风险** | 单人开发者没有「无限预算」或「公司报销」——LLM API 费用是个人实际成本。以 GPT-4o 的价格估算：一次 Compact Cycle ~5K input / ~1K output = ~$0.025；一次 Exploratory Cycle (20 步 ReAct) ~50K input / ~10K output = ~$0.2；一轮完整实验（1 次 Compact + 1~2 次 Exploratory + ExecutorAgent + Reflection = 约 $0.5~1.0）。10 轮实验迭代即可达 $5~10。如果调试阶段天天跑 50 轮，月账单可达 $500+。**文档未考虑调试阶段的高频调用成本。** |
| **需核对/补充** | ① 补充基于参考项目的典型认知成本估算表（Token/轮、美元/轮）；② 设置 CognitiveBudget 明确默认值（如单 Session 上限 $50）；③ 考虑调试模式使用便宜模型（如 GPT-4o-mini / Claude Haiku）进行 functional test，准入后再切主力模型；④ 评估是否所有 Agent 都需要最强模型（ReviewerAgent 是否可以降级）。 |

### H6. AI 生成代码的质量管控机制完全缺失

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档未讨论代码质量管控。未涉及：代码规范和风格一致性（ruff / black / mypy 配置和 CI 自动检查）、AI 生成代码的逻辑复核 checklist、安全漏洞专项排查（prompt injection、shell injection、路径穿越）、重复代码 / 模块重构的识别机制。 |
| **潜在风险** | AI 编程助手在单个文件中表现良好，但在跨文件、跨模块场景下频繁出现：① 重复实现已有功能（由于不熟悉现有代码，重新写一遍已有工具函数）；② 不一致的命名风格和架构约定（有的用 class-based，有的用 functional）；③ 引入不必要的依赖（AI 倾向于使用第三方库而非内置库）；④ 生成「看似合理但实际无效」的代码（进程管理、GPU 交互、信号处理等系统编程场景尤为严重）。单人没有专门的 code review 角色来识别这些问题——AI 自己 review 自己往往无法发现深层逻辑错误。 |
| **需核对/补充** | ① 在 AGENTS.md 或项目 README 中建立 AI 编程助手的规则文件（`CLAUDE.md` / `AGENTS.md`），约束代码风格和常用模式；② 配置自动化质量门禁（ruff / mypy / pytest coverage / safety scan），要求在 `verify.sh` 中执行；③ 建立 AI 生成代码的 review checklist：是否存在硬编码路径？异常处理是否完善？是否有未使用的 import？是否重复了现有实现？④ 建议核心逻辑（Coordinator 决策流、ResourceLease、Sentinel 检测链）由开发者手写主体架构，AI 仅辅助填充。 |

---

## 中优先级风险

### M1. Agent 配置 / Prompt 版本管理方案缺失

| 维度 | 内容 |
|------|------|
| **问题定位** | `01_大框架` 第 6 节提到 AgentFactory 使用不同 system prompt 和 tool_profile，`07_收敛` 第 5 节 StrategyOverlay 说 prompt 变更要版本化和可回滚。但没有给出 system prompt 和 tool configuration 的具体文件组织方式、版本管理机制、回滚流程。 |
| **潜在风险** | 单人开发者将面临以下混乱场景：修改了一个 Agent 的 prompt → 忘记备份旧版本 → 发现新 prompt 效果更差 → 无法回滚 → 靠记忆重写旧版 → 不一致。这一问题在 AI Agent 开发中极其常见，且随系统演化呈指数级恶化。 |
| **需核对/补充** | ① 明确 prompt 的存储方式（YAML 文件 vs 数据库）；② 建立 prompt 版本管理机制（git 管理 + semantic version，每次 prompt 修改单独 commit）；③ 建立 prompt 效果记录表（每次 prompt 修改记录：修改内容、影响 Agent、测试结果、Token 变化）。 |

### M2. 工具调用全链路缺少异常兜底设计

| 维度 | 内容 |
|------|------|
| **问题定位** | `01_大框架` 第 6 节 Agent 的 tool_profile 提及 shell、filesystem 等工具，`03_Coordinator` 第 3.1 节 Idea Tree 操作工具有 9 个 mutation 方法。但所有文档均未对以下工具调用失败场景设计兜底：工具超时（LLM 发起文件写操作但 hang）、工具返回格式不符合预期、工具抛出意外异常、工具访问权限冲突（并发写同一 Idea Tree node）、LLM 发起恶意/异常参数的工具调用。 |
| **潜在风险** | AI Agent 工具调用的失败模式是**发散的、不可穷举的**。单人开发者无法预判所有失败路径。如果每个工具调用没有 timeout + retry + fallback + audit 四层兜底，系统会频繁出现「Agent 卡在某个工具调用上」、「静默失败」、「状态不一致」等难以排查的问题。 |
| **需核对/补充** | ① 建立统一的 ToolCallWrapper：timeout（全局 + per-tool 可配）、retry（最多 N 次 + exponential backoff）、fallback（空返回 vs error message vs 降级）、audit（每次调用参数 + 结果 + 耗时）；② 对每个工具声明可能抛出的异常类型和处理策略；③ 所有 mutation tool 使用 `expected_revision` 乐观锁防止并发冲突。 |

### M3. 单人视角的「可调试性」设计缺失

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档。Artifact 目录（`01_大框架` 第 14 节）虽然丰富（~30 个文件），但未从**排查问题**的角度设计调试工具。缺少：事件回放工具（`events.jsonl` 能否支撑复现 Agent 决策链？）、Agent 决策日志（每次 LLM 调用的 raw input/output dump）、关键路径的可视化（Idea Tree 能否 dump 为可读的 graph？）、Session 状态一键导出（用于问题报告）。 |
| **潜在风险** | 单人开发模式下没有第二人帮忙看代码、查日志。系统上线后如果出一个「Coordinator 选择了错误的 action」或者「Champion 晋升逻辑异常」，开发者需要从几十个 JSON 文件和数千行日志中手动追溯——这可能需要数小时甚至数天。AI 编程助手在「理解系统现有状态并辅助调试」方面的能力远弱于代码生成。 |
| **需核对/补充** | ① 建立单条命令即可获取的 `session-debug-dump` 工具：一键导出 Session 全部状态 + 最近 N 个事件 + Agent 决策链；② 为每次 CognitiveCommit 记录 LLM raw input/output（可选，默认开启但可关闭以节省成本）；③ 核心数据流动路径（Observer → Coordinator → Executor → Job → Validity → Commit）需要有 trace_id 贯穿始终。 |

### M4. GPU/环境调试的高成本风险未考虑

| 维度 | 内容 |
|------|------|
| **问题定位** | `04_ExperimentJob` 和 `02_ExperimentSession` 涉及 GPU compute probe、环境 build、nvidia-smi 检测等操作。`05_ExperimentJob` 第 9.3 节 Sentinel 故障注入需要 fixture 训练脚本。 |
| **潜在风险** | 单人开发者调试 GPU 相关代码的**单次迭代成本极高**：一次环境 build 可能 2~10 分钟（uv sync + pip install），一次训练运行可能 5~30 分钟。如果 ExecutorAgent 或 Sentinel 有 bug，一次修改 → 重建环境 → 重新运行 → 检查结果 = 30~60 分钟。单人无法像团队开发那样并行验证多条路径。AI 编程助手在调试 GPU 进程管理、信号处理、cgroup 等系统级问题时**几乎没有帮助**——这些场景下的 AI 生成代码质量往往低于一般业务代码。 |
| **需核对/补充** | ① 在开发环境准备阶段建立「离线测试模式」：所有 GPU 操作可 mock（mock_nvidia_smi、mock_torch_cuda），无需真实 GPU 即可验证逻辑；② 集成测试夹具中训练脚本的运行时间控制在 30 秒内；③ 确定一条「最小开发循环」：改代码 → 运行 mock 测试（<10 秒） → 通过后再跑真实 GPU（减少非必要的长循环次数）。 |

### M5. 确定性代码部分编码量被低估

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档虽大量依赖 LLM Agent，但确定性代码部分同样庞大：Idea Tree store（CRUD + 乐观锁 + revision + event + 并发控制）、Job store（claim/retry/heartbeat/timeout 全套）、ResourceLease allocator（nvidia-smi 解析 + 原子 lease + expiry + recovery）、Sentinel（10+ detector chain + heartbeat + pid 跟踪）、WorktreeManager（create/cleanup/protected hash）、SEARCH/REPLACE 四层策略栈（400+ 行逻辑代码）、EvaluationContract SHA256（三层防御）。 |
| **潜在风险** | AI 编程助手在 CRUD 和数据模型代码上效率很高，但在并发控制、资源管理、系统编程方面表现不稳定。单人开发者需要花大量时间 review 和修正 AI 生成的系统代码。Job 和 ResourceLease 的并发 bug 在测试中极难发现，线上才暴露，且一暴露就是数据损坏或资源死锁。 |
| **需核对/补充** | ① 对并发控制密集的模块（Job store、ResourceLease）建议手写核心逻辑，AI 只生成测试；② 为所有 store 操作编写「并发冲突」的故障注入测试（模拟两个 worker 同时 claim、模拟 lease 过期后同时释放）；③ 评估是否可以使用 SQLite 的事务机制简化并发控制（所有 store 操作通过 WAL 模式的事务解决竞争条件）。 |

### M6. 没有 SQL/NoSQL 选型与状态持久化方案

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档使用文件系统（JSON/JSONL 文件）作为持久化方案。`01_大框架` 第 4 节的所有 Store（SessionStore、JobStore、AttemptStore、EventLog、ChampionStore）均以 Artifact 目录中的文件形式实现。 |
| **潜在风险** | JSONL 文件：① 不支持条件查询（查询「所有 failed 的 attempt」需全量扫描）；② 不支持事务（写入部分数据后崩溃 → 数据损坏）；③ 不支持索引（当 attempt 数量达 100+ 时查询延迟明显）；④ 不支持并发写入（多 Worker 写同一 JSONL 需文件锁）。单人开发者将不得不在项目中期引入数据库迁移——这是开销极高的晚期架构变更。 |
| **需核对/补充** | ① 是否使用 SQLite（轻量、事务、单文件、高并发读？WAL 模式支持并发写？）替代 JSONL 文件；② 如坚持文件系统，需补充每个 Store 的并发写入方案（append-only with flock vs 每个 record 独立文件）；③ 考虑性能指标：预期 Session 的 Attempt 数上限（50？500？5000？），不同量级下的存储方案不同。 |

---

## 低优先级待确认项

### L1. 代码可交接性设计缺失

| 维度 | 内容 |
|------|------|
| **问题定位** | `00_README` 虽然是单人项目，但未讨论代码的可交接性。 |
| **潜在风险** | 单人开发的一大隐性风险是「开发者被 bus factor 命中」——项目突然需要交接时，AI Agent 的 prompt 配置、Agent 行为调试记录、性能调优的 trial-and-error 历史全部在开发者脑中。这种情况下的交接成本远超传统项目（因为 Agent 行为依赖大量隐式的 prompt 工程经验）。 |
| **需核对/补充** | ① 关键 prompt 变更和 Agent 行为观测记录到 `notes/` 中的日志文件；② AI 辅助开发的「调试记录」做简要文档化（尝试了什么方案、为什么有效/无效）；③ 至少项目 README 应说明系统的高层架构和关键设计决策。 |

### L2. 缺少「Agent 效果退化」的回归检测

| 维度 | 内容 |
|------|------|
| **问题定位** | `07_收敛` 第 8 节有质量指标，但全是系统层面的（job recovery rate、valid attempt rate），没有对单个 Agent 行为质量的回归检测（如：Coordinator 修改 prompt 后，同一组测试用例的 CycleDecision 准确率是否下降？）。 |
| **潜在风险** | Agent prompt 的一个小改动可能在某处修复了一个问题，但在另一个场景下引入了退化。AI Agent 的「Prompt 脆弱性」是已知问题：微小的 prompt 措辞变化可能导致输出质量剧烈波动。单人开发者没有人力维护一套完整的 Agent 行为回归测试集。 |
| **需核对/补充** | ① 建立一组固定的 Agent 测试 prompt（如：给定一个已知的 OutcomeCard，验证 Coordinator 是否能输出合理的 CycleDecision），在 prompt 修改后运行；② 使用固定模型版本（model pinned）做回归测试，排除模型升级带来的干扰。 |

### L3. 缺少对 AI 编程助手服务不可用的应对方案

| 维度 | 内容 |
|------|------|
| **问题定位** | 全文档依赖 AI 编程助手生成代码，但未讨论 Codex/Claude Code 服务不可用时的开发连续性方案。 |
| **潜在风险** | API 服务中断、账号余额不足、网络故障时，单人开发者编码效率可能降至 10%~20%，且核心问题（bug 定位、架构设计）的进度完全停滞。 |
| **需核对/补充** | ① 本地保留一份可离线运行的代码生成模型（如 CodeGemma / DeepSeek-Coder）作为备用；② 核心架构文档和模块接口定义提前固定，离线时只需按接口实现。 |

---

## 问题汇总

| 优先级 | 数量 | 编号 |
|--------|------|------|
| 高 | 6 | H1 ~ H6 |
| 中 | 6 | M1 ~ M6 |
| 低 | 3 | L1 ~ L3 |
| **合计** | **15** | |

---

## 最终建议

1. **立即评估 DeepAgents 框架的可用性**——这是整个系统的命脉。若不可用，建议用 LangGraph 替换，并重新评估开发周期（至少 +50%）。
2. **将第一版范围裁剪为「一轮实验闭环」**——删除 Exploratory Cycle、ReviewerAgent、StrategyDiagnostic、多 seed noise floor，聚焦 Coordinator Compact Cycle + Executor + Job + Validity 的最小闭环。
3. **建立「AI 生成代码质量管控流程」**——在 `verify.sh` 中强制 ruff + mypy + pytest + 核心模块 hand-review。
4. **设置认知成本预算红线**——调试阶段用便宜模型（~$0.15/1M token），调试通过后再切主力模型，单 Session 预算设硬上限。
5. **所有并发敏感模块手写核心逻辑**——Job Store、ResourceLease、WorktreeManager 由开发者手写核心状态机，AI 只补测试和辅助代码。
