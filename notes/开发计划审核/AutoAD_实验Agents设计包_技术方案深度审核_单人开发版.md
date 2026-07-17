# AutoAD 实验 Agents 设计包 — 技术方案深度审核报告（单人开发 + AI 编程助手版）

> 审核日期：2026-07-16  
> 审核范围：`notes/后端开发/AutoAD_实验Agents设计包/`（共 8 个 markdown 文件）  
> 审核视角：单人开发 + 重度依赖 AI 编程助手（Codex/Claude Code）  
> 核心关注：单点故障风险、AI 生成代码质量风险、Agent 系统不确定性风险

---

## 🔴 高优先级风险

### H1 —「DeepAgents」框架为整系统核心但来源完全未定义，单人无法兜底

**问题定位：** `01_实验Agents大框架.md:381` 起全面依赖 `create_deep_agent()`、DeepAgents 的 checkpoint/SummarizationMiddleware/工具系统。整个 ResearchCoordinator、ExecutorAgent、ReflectionAgent、HealthDiagnosisAgent 均构建在其上。Agent 的持久化、工具调用、中间件、权限控制全部依赖该框架。

**潜在风险（单人开发叠加效应）：**
- 若 DeepAgents 为 AI 编程助手生成的全新框架，单人开发者在排期内无法交付一个有生产质量的 Agent 框架 + 6 个业务 Agent + 全套确定性治理代码
- 若 DeepAgents 为现有框架，单人需同时消化框架源码 + 理解其 Checkpoint 序列化/反序列化细节 + 排查框架 bugs，调试成本极高
- 框架一旦出现与业务需求不匹配的设计鸿沟（如 SummarizationMiddleware 裁剪掉决策关键信息），单人需深入框架源码修改，风险不可控
- 若 DeepAgents 的 checkpoint 机制存在序列化兼容性问题（常见于 Pydantic V1→V2、Python 版本升级），单人排查周期难以预估

**需核对/补充：**
- 明确 DeepAgents 是否已在现有代码库中存在并经过测试（非 AI 生成的新写框架）
- 如为自研框架，需给出该框架单独的开发排期与测试策略，不能将其视为零成本基础设施
- 评估单人完全掌握该框架所需的学习成本，并计入排期

---

### H2 — Coordinator 持久运行模式与单人调试能力不匹配

**问题定位：** `03_ResearchCoordinator.md:25-27` 要求 Coordinator「可以持久」，采用 DeepAgents Persistent/Checkpoint 模式运行，生命周期绑定的 ExperimentSession。同时 `03_ResearchCoordinator.md:287-293` PR-02E 定义多种崩溃恢复场景（checkpoint 存在/缺失/不一致/pending attempt 重连）。

**潜在风险：**
- 持久运行的 Agent 意味着状态在时间线上积累，单人开发者在开发阶段很难构造完整的 checkpoint 状态矩阵进行测试
- Checklist 中的 4 种恢复场景仅是「存在/缺失」二值组合，实际运行时状态组合远更复杂（如 checkpoint 内容部分损坏、LLM message 历史与 IdeaTree revision 矛盾、Session 已 FAILED 但 checkpoint 为 READY）
- 持久 Agent 的调试需要「kill -9 + 重启验证恢复逻辑」，单人手动执行这些测试非常耗时，难以覆盖全组合
- AI 编程助手对持久化 Agent 恢复逻辑的生成质量通常较差（因果链长、涉及多个持久化 store 的状态还原），容易生成表面正确但边界有 bug 的恢复代码

**需核对/补充：**
- 设计阶段限制持久 Agent 的状态空间（如明确第一版仅支持「clean stop」恢复，不支持 crash 恢复）
- 为持久 Agent 的每个恢复路径编写确定性单元测试（用 mock checkpoint 和 mock store），不依赖人工手动构造
- 考虑开发阶段是否可以先绕过 DeepAgents checkpoint，用简化版 `load_session() + rebuild_tree()` 替代

---

### H3 — 多 Agent 协作的竞态条件与死锁风险未评估

**问题定位：** 全文档定义了 7 种 Agent 角色（Coordinator、IdeaExplorer、Reviewer、Executor、Reflection、HealthDiagnosis、StrategyDiagnostic），共享多个持久化 Store（IdeaTree Store、CognitiveCommitLedger、ChampionStore、JobStore、ResourceLease）。但所有 Store 的并发访问控制仅在 `03_ResearchCoordinator.md:61-68` 提及「expected revision + idempotency key + atomic write」，无具体锁机制设计。

**潜在风险：**
- 多个 Agent 可能并发读写同一 Store：如 Executor 执行时 ReflectionAgent 同时读取 IdeaTree；Sentinel 运行中 Coordinator 开始新一轮决策
- 单人开发者需自行实现乐观锁 / 悲观锁 / 文件锁，任何一个 Store 的并发 bug 都会导致状态不一致，且非常难以复现和调试
- AI 编程助手在「多进程并发 + 文件系统 atomic write」场景下生成的代码经常遗漏边界（如 Linux NFS 不支持 `os.rename` 的原子性、`json.dump + write` 非原子操作导致读半成品）
- 文档多处提到「Worker repeat claim 时不能重复创建环境」(`02_ExperimentSession.md:147`)、「duplicate idempotency」(`05_ExperimentJob.md:352`)，但未给出可落地的去重方案（DB unique constraint / 文件锁 / redis lock）

**需核对/补充：**
- 明确各 Store 的并发访问模型（单进程单线程 / 单进程多线程 / 多进程），以及对应的锁选型
- 为每个并发写操作设计幂等性方案和冲突检测，不能仅停留在「expected revision」概念层面
- 评估单人能否在第一版中完成全部 Store 的并发安全实现，否则应压缩并发场景（如限定 Coordinator 运行时其他 Agent 不修改 Tree）

---

### H4 — AI 编程助手生成 Agent 代码的质量管控机制完全缺失

**问题定位：** 全文档（8 个文件）未提及任何 AI 生成代码的质量管控措施。文档重度依赖代码生成（SEARCH/REPLACE 复用 aider、Sentinel 复用 SWE-Together、StuckDetector 复用 software-agent-sdk），但未回答以下问题：
- AI 生成的 Agent prompt、tool call 处理、状态流转代码如何在开发阶段被验证？
- 如何确保 AI 编程助手生成的 Coordinator decision loop 不会陷入无限循环？
- AI 编程助手可能在不同模块中生成重复的 Store 操作代码（如两个 Agent 各自实现 IdeaTree mutation，导致 revision 逻辑不一致）

**潜在风险：**
- 单一开发者很难对 AI 编程助手逐段生成的 Agent 代码做全局架构一致性审核
- AI 编程助手在「生成一个 Agent 的 system prompt + tool list」时容易遗漏权限校验、输入验证等安全代码
- 当 Agent 出现非预期行为时，开发者无法区分「是 prompt 设计问题、模型推理问题、还是 AI 生成的代码 bug」——排查路径呈三叉发散
- 不同 session 中 AI 生成的代码容易产生「飘移」：同一功能（如 `tree_add_node`）在多处被重新实现，细节不一致

**需核对/补充：**
- 建立 AI 生成代码的强制审核清单（至少包括：输入校验、异常处理、权限控制、幂等性、日志）
- 限定 AI 编程助手的使用范围：核心 Store 操作由开发者手写，AI 仅生成 Agent call 层的胶水代码
- 增加 CI 阶段的架构一致性检查（如 lint 规则禁止重复实现同一 Store 操作、类型检查确保协议一致）

---

### H5 — Agent 行为不可预测性未被纳入排期与风险缓冲

**问题定位：** 全文档将 Agent 行为视为确定性模块对待。如 `03_ResearchCoordinator.md:112-127` Compact Cycle 预期「一次 LLM 即可完成」，`05_ExperimentJob.md:390` 预期「正常训练 0 次 LLM health call」。但未在任何位置评估 Agent 行为不确定性对开发排期的影响。

**潜在风险：**
- AI Agent 系统的典型开发规律：**20% 时间开发功能，80% 时间调 prompt 和处理边角情况**。文档中的 20+ PR 排期如果按传统开发节奏估算，严重不足
- Compact Cycle 的「一次 LLM 返回 CycleDecision」在实际中经常需要 3-5 次调试才能稳定输出符合 schema 的 JSON
- Sentinal 的「已知 OOM 0 次 LLM 调用」在实现中需要大量调试才能达到（未知错误模式需要反复完善 detector chain）
- 单人开发者在 Agent 行为调试时容易陷入「改 prompt → 试运行 → 观察 → 再改 prompt」的循环，单次循环耗时 10-30 分钟，非线性的调优时间难以预估

**需核对/补充：**
- 将「Agent 行为调试与效果收敛」作为独立排期项列入，建议不低于总排期的 40%
- 为每个 Agent 的「首次可用」和「稳定可用」设定两个不同验收标准，避免无限调优
- 建立 Agent 行为回归测试集，防止修 A Agent 的效果时破坏 B Agent

---

### H6 — 单人开发模式下的架构一致性保障方案完全缺失

**问题定位：** 全文档涉及 7 类 Agent、8 种 Store、6 种 Agent Spec 接口、复杂的事件系统、多层级 Budget。分布式约束体现在多处：
- `01_实验Agents大框架.md:397-412` AgentTaskSpec 定义了 10 个字段，作为所有 Agent 的通用接口协议
- `04_ExecutorAgent.md:127-171` 定义 PreApplyPatchGate / PostApplyDiffGuard 多级 Gate
- `07_收敛.md` 定义 ConvergenceMonitor / StuckDetector / StrategyOverlay

**潜在风险：**
- 单人开发周期可能跨数周甚至数月，前期定义的 AgentTaskSpec 协议在后期实现时容易被「临时绕过」——新增字段但未更新所有调用方、Gateway 被跳过
- AI 编程助手在不同时间片的 session 中没有跨模块记忆，容易生成与已有协议不兼容的代码（如 IdeaTree 的 `tree_add_node` 在某处接受 `dict` 而在另一处接受 `BaseModel`）
- 接口版本漂移在单人开发模式下最隐蔽——没有第 2 个开发者 review PR，不一致可能在集成时才暴露
- `06_实验有效性.md:88-98` 明确「第一版不采用 VERIFIED/UNVERIFIED/INVALID，改用 4 个布尔 check」，这个决策需要在多个模块中统一贯彻，AI 编程助手容易在后续代码中「回退」到旧枚举

**需核对/补充：**
- 制定接口契约的强制版本管理机制：核心接口（AgentTaskSpec、IdeaNode、AttemptRecord 等）的第一版稳定后用 Pydantic model 锁定，AI 生成的代码必须通过 model_validate 校验
- 在 CI 中引入「架构合规性检查」：如 lint 规则禁止在新的 Agent 中直接调用 `create_deep_agent()` 而非通过 `CognitiveTaskRunner`
- 每周执行一次「接口一致性快照」：对比所有 Store/model 定义的一致性

---

## 🟡 中优先级风险

### M1 — 工具调用的全链路闭环缺失关键环节

**问题定位：** `01_实验Agents大框架.md:397-412` 定义了 AgentTaskSpec 和 CognitiveTaskRunner，`04_ExecutorAgent.md:56-65` 列出 ExecutorAgent 的工具限制（Filesystem 限制到 worktree、shell 工具白名单、无网络默认、SEARCH/REPLACE 编辑）。但以下链路缺失：
- **参数校验**：LLM 生成的 tool call 参数如何校验（如路径穿越检查、int 溢出、非法字符）
- **失败重试策略**：工具调用失败后是否重试、重试次数、退避策略
- **超时处理**：每个 tool 调用的超时时间如何设置、超时后 Agent 行为
- **异常兜底**：工具抛出意外异常时的结构化错误格式，Agent 如何消费

**潜在风险：**
- LLM 经常生成路径参数为 `../../etc/passwd` 格式，若无参数校验和安全过滤，单人调试时可能无意破坏宿主机文件
- 工具调用失败后 Agent 可能沉默重试（重复相同 call），消耗 Token 而无法推进任务
- 超时处理缺失会导致单个 tool call 卡死整个 Agent 循环

**需核对/补充：**
- 建立 Tool Call 的全链路规范（register → validate → execute → retry → timeout → fallback → structured error）
- 为每个工具明确超时值、重试次数、输入校验规则
- 在 ExecutorAgent 的 shell 工具中硬性实施 shell=False（已提及但未在所有 Agent 中贯彻）

---

### M2 — Agent 能力边界与人工介入触发条件未定义

**问题定位：** 全文档仅 `03_ResearchCoordinator.md:145-147` 列出 Exploratory Cycle 的触发器（conflict/stagnation/low confidence/large pivot 等），`06_实验有效性.md:254-263` 列出 ReflectionAgent 的触发器。但：
- 没有定义「哪些情况下系统应停止并等待人工」
- Coordinator「stop proposal」(`01_实验Agents大框架.md:652`) 由确定性 StopPolicy 验证，但未说明验证不通过后怎么办
- 没有人工介入接口（如用户 review idea 后再允许执行、用户确认 champion 后再合并）

**潜在风险：**
- 实验系统可能在完全错误的假设上持续运行数十个循环，消耗大量 GPU 资源和 Token 费用，直到预算耗尽
- 单人开发者无法 24 小时值守系统，需要清晰的人工介入边界条件
- AI Agent 常见的「failing silently」行为（表面上在推进，实际上在做无意义操作）无拦截

**需核对/补充：**
- 定义第一版的人工介入点：至少包括「Coordinator stop proposal 待确认」「champion 晋升需人工批准」「预算临界时通知」
- 实现「watchdog timeout」——若 Agent 连续 N 轮无有效输出，自动暂停并通知开发者
- 定义「failing silently」的检测规则：连续工具调用失败 / 连续相同决策 / 状态未变更但 Token 持续消耗

---

### M3 — 评测体系不可量化，AI Agent 效果无验收标准

**问题定位：** 各文件的「验收标准」以定性描述为主（如 `03_ResearchCoordinator.md:354-361`「Coordinator 常规循环一次 LLM 即可完成」「10+ cycles 后树仍可追溯」），缺乏可量化的指标定义。

**潜在风险：**
- 「一次 LLM 即可完成」没有定义「完成」的标准（输出符合 schema 就算？还是决策质量达到人工水平？）
- 没有定义 Agent 的「幻觉率」「无效决策率」「Token 浪费率」等关键效率指标，无从判断系统是否在有效运行
- 单人开发者无法客观判断「这个 Agent 是否已调好」，只能凭感觉继续调优，导致时间黑洞
- 若后续发现 Coordinator 决策质量低于预期，无法定位是 prompt 问题、模型问题还是数据输入问题

**需核对/补充：**
- 为每个 Agent 建立最低可接受的量化指标（例如 Coordinator：结构化输出有效率达到 95% 以上、重复 idea 率低于 20%；Executor：SEARCH/REPLACE 首次成功率 > 70%）
- 建立 Agent 决策质量的离线评测集（固定输入 + 预期输出 + 自动比对）
- 评测集应纳入 CI，每次 prompt/配置改动后自动运行，防止回归

---

### M4 — Token 成本模型与预算控制不可落地

**问题定位：** `01_实验Agents大框架.md:687-716` 定义了 ComputeBudget 和 CognitiveBudget，给出简单的硬限制实现。但：
- 未给出单次完整 research cycle（OBSERVE→IDEATE→SELECT→DISPATCH→DECIDE）的预估 Token 消耗
- 未评估 Exploratory Cycle 的 Token 消耗（可能调用多个子 Agent + 长上下文 ReAct）
- 没有任何 Token 成本的预警机制（预算斜率监测、异常消耗检测）
- `01_实验Agents大框架.md:715` 刻意明确「不对账目做有效/无效分类」

**潜在风险：**
- 单一 Agent 的一个 Exploratory Cycle 可能消耗数十万 Token（多个子 Agent 调用 + 长上下文），如果循环失控，单 Session 成本可能在几小时内达到数百美元
- 单人开发者如果没有 Token 成本预警系统，可能在收到账单时才发现成本失控
- 「不对账目做有效/无效分类」意味着无法识别「Agent 在空转浪费 Token」——这恰是 AI Agent 系统最大的成本黑洞
- AI 编程助手生成的 Agent 循环代码经常遗漏 Token usage tracking（不在每次调用后写入 `llm_usage.jsonl`）

**需核对/补充：**
- 建立每轮 research cycle 的 Token 消耗预算基线，监控偏离度
- 实现 Token 消耗的「斜率检测」：若单位时间内消耗速率超过阈值（如 2× 基线），触发预警或暂停
- 至少在第一版中实现调用日志中的 Token recording 完整性验证（每次 LLM 调用必须对应一行 usage log）

---

### M5 — Prompt 版本管理与回滚方案缺失

**问题定位：** `01_实验Agents大框架.md:381-390` 提到不同 Agent 使用不同的 system prompt，`07_收敛.md:140-164` StrategyPolicy 的 prompt overlay 要求「版本化、audit、rollback」。但全文档：
- 未定义 Prompt 模板的存储位置和格式
- 未定义 Prompt 变更的版本化管理方式（git? 数据库? 配置文件?）
- 未定义回滚操作的具体流程和影响范围
- 未评估 prompt 变更对其他 Agent 的副作用

**潜在风险：**
- 单人开发者在调优 Coordinator prompt 时，可能无意改变了对其他 Agent 的行为（如果 prompt 文件被共享引用）
- 未版本化的 prompt 变更无法回滚，一旦发现「调优后 Agent 效果变差」，无法恢复
- AI Agent 系统的 prompt 经常需要「微调」——改一个词可能引发行为质变，无版本管理意味着每次调整都是赌博
- `StrategyPolicy` 要求 overlay「只追加不可改」，但若 base prompt 本身的 bug 被后续发现，无法修改

**需核对/补充：**
- 将所有 Agent 的 system prompt 统一纳入 git 管理的模板文件目录（非代码硬编码），每次变更生成独立的 commit
- 为 prompt 变更建立配套的回归测试（在评测集上验证效果未退化）
- 定义 StrategyPolicy overlay 与 base prompt 的合并策略（优先级规则、冲突处理）

---

### M6 — AI 编程助手依赖的单点风险无兜底方案

**问题定位：** 全文档假设 AI 编程助手辅助开发，但未评估其服务不可用时的开发连续性。文档引用的外部代码仓库（aider/SWE-Together/MiMo/software-agent-sdk/AutoSOTA）也都依赖 AI 编程助手进行代码阅读和适配。

**潜在风险：**
- Codex/Claude Code API 服务不可用或限流时，单人开发者进度直接归零
- AI 编程助手在代码生成中引入设计缺陷（如「完美但不可维护」的抽象），单人需要花双倍时间重构
- AI 编程助手的输出不稳定——同一 prompt 两次生成的代码风格不同，导致代码库风格碎片化
- 项目重度依赖「AI 阅读理解外部代码仓库语义」，若 AI 助手对某个仓库理解错误（如误判类继承关系），生成的适配代码可能在集成时才暴露问题

**需核对/补充：**
- 建立「无 AI 助手兜底模式」：核心工具（WorktreeManager、Patch 协议、ResourceLease）应可纯手写实现，不应深度依赖 AI 生成
- 对 AI 编程助手生成的代码进行「可维护性标记」：标注哪些是 AI 生成的，方便后续人工审核和重构
- 确认在 AI 助手不可用时，单人开发者能否至少完成「修复 bug」级别的紧急维护

---

## 🟢 低优先级待确认项

### L1 — 测试策略不匹配单人开发模式

**问题定位：** 各文件的「检验方案」详实但覆盖面广（单元测试 + 集成测试 + 故障注入 + fixture 代码任务），未评估测试环境的搭建成本和维护成本。

**需确认：** 故障注入测试（如 `05_ExperimentJob.md:369-381` 的 9 个故障注入场景）需要构造模拟 GPU、模拟训练进程、模拟 heartbeat 停摆等环境，单人搭建这些测试环境的成本不亚于开发本身。建议优先用 mock 覆盖 80% 故障场景，真实 GPU 环境只做关键路径验证。

---

### L2 — 日志体系未设计，单人排查效率难保障

**问题定位：** 全文档要求「artifact/event/hash」，定义了 EventLog、EventStore、`events.jsonl`，但未定义日志级别、结构化格式、Trace ID 传递、跨模块请求追踪。

**需确认：** Agent 系统的排查链路通常涉及「Session Init → Environment Setup → Coordinator Decision → Executor Edit → Job Run → Sentinel Monitoring → Result Integration → Reflection → Next Decision」，至少 9 个环节。无 Trace ID 串联时，单人开发者定位一个「Executor patch 通过但 evaluation 失败」的问题需要在 9 个日志文件中手动关联时间戳，成本极高。建议在第一版引入简单的 Correlation ID（在 Session 创建时生成，透传所有子模块）。

---

### L3 — 数据安全与用户隔离设计缺失

**问题定位：** 全文档未提及多用户场景下的数据隔离、用户输入数据保护、工具调用的权限鉴权。

**需确认：** 虽然目前为单用户版本，但 ExecutorAgent 的 shell 工具可执行任意命令、Worktree 可修改仓库文件、Environment 可安装任意包——若未来暴露为服务，这些权限没有分层。建议至少在第一版中记录所有工具操作的审计日志（who/ what/ when/ result），为后期权限管控做准备。

---

### L4 — 开发者单点知识壁垒已形成

**问题定位：** 文档高度抽象（8 个文件，约 3000 行文档），涉及 28 个参考项目的设计吸收、7 种 Agent 角色、多种 Store 和 Gate。

**需确认：** 当前仅 1 名开发者，所有设计决策都在这份文档中。但这套系统的复杂度远超一般单开发者的维护能力。建议：核心 Store 的 schema 变更、Agent 的行为决策逻辑、Gate 规则这三类「一旦理解断层则无法维护」的关键知识，需要在代码注释和文档中明确标注决策理由（例如「为什么 protocol shift 用 SHA256 而非 mtime？」）。考虑为每个 PR 编写简短的决策记录（ADR, Architecture Decision Record）。

---

### L5 — 第一版范围可能仍偏大

**问题定位：** `01_实验Agents大框架.md:764-793` 列出第一版必须具备的 18 项能力，结合 20+ PR 拆解（从 001A 到 06F），覆盖从环境准备到收敛检测、从 GPU 监控到端到端验收的全链路。

**需确认：** 单人 + AI 编程助手估算：假设 AI 提速 2-3x，但调试 AI Agent 效果的时间不可压缩。18 项能力的开发 + 调试 + 效果收敛，按保守估计：每项能力 3-7 天开发 + 3-5 天调试收敛，总排期约 110-215 天（不考虑阻塞和返工）。建议明确 MVP 的边界——至少前 8 项能力形成一个可运行闭环（Session → Env → Baseline → Coordinator single cycle → Executor → Job → Status → stop），之后的作为第二阶段。

---

## 📋 整体技术风险评级与总结

| 维度 | 风险等级 | 核心判断 |
|------|---------|---------|
| Agent 核心架构 | 🟡 中 | Agent 分工和状态持久化设计清晰，但并发控制、工具调用闭环、人工介入边界缺失 |
| 技术选型与依赖 | 🔴 高 | DeepAgents 未定义来源为核心盲区，5 个外部仓库未锁 License 与版本 |
| 单人开发专项 | 🔴 高 | 架构一致性保障、AI 代码质量管控、知识沉淀三项关键机制全部缺失 |
| 评测与质量 | 🔴 高 | 无量化验收指标、无回归测试套件、Agent 效果不可衡量 |
| 部署运维 | 🟡 中 | 日志和监控有概念设计，但 Trace ID、成本预警、异常告警无落地方案 |
| 排期可行性 | 🔴 高 | 工作量低估（20+ PR + Agent 效果调优周期），人力过载（1 人全链路） |

**总体风险评级：高（进入开发前需先解决 H1、H4、H6）**

**三项需优先解决的改进方向：**

1. **解决 DeepAgents 依赖盲区**（H1）——确认框架来源与状态，或重新评估是否可先用 LangGraph/AutoGen 等成熟框架降低风险
2. **建立 AI 编程助手的代码质量管控规则**（H4）——限定 AI 生成范围 + 强制审核清单 + 架构一致性检查
3. **缩减第一版范围至可落地的 MVP 闭环**（L5）——确保单人可在可预期时间内交付一个端到端可运行的实验闭环，避免 18 项能力全部做到一半即耗尽资源
