# AutoAD 实验 Agents 设计包 · 参考项目对比与差距分析

> 审核方式：逐一审查计划声称"直接复用"的参考项目的实际代码，与计划描述做逐项对比
> 覆盖参考项目：Arbor / SWE-Together / AI-Scientist / MiMo(OpenCode) / AutoSOTA / software-agent-sdk / claude-code-internals / academic-research-skills
> 审核日期：2026-07-16

---

## 核心发现

**评估结论：计划文档中对参考项目的"直接复用"描述与实际情况存在系统性偏差。** 大部分声称"直接复用"的代码在实际项目中要么不存在、要么深度耦合于宿主项目的框架/类型系统/事件模型，不可独立抽取。单人开发者 + AI 编程助手在逐项适配这些外部依赖时将面临大量未预期工作。

此外，参考项目中存在多个有价值的模式未被计划吸收。以下逐项展开。

---

## 第一类：计划声称"直接复用"但实际不可复用的引用

### GAP-01：AutoSOTA `record_score.sh` — 文件在仓库中不存在

**计划声称（Plan 05 §2.1）：**
> 三层 SHA256 防御（直接复用 AutoSOTA）来源：`/root/autodl-tmp/repos/AutoSOTA/cli_guide.md` lines 735-802；`record_score.sh`。

**实际情况：**
- `record_score.sh` 在 AutoSOTA 仓库中不存在（已搜索全仓库，无匹配文件）
- `cli_guide.md` 中存在描述性文档，但描述的脚本属于 AutoSOTA 的 **PyPI 安装包**（`pip install autosota`），不在开源代码库中
- 该脚本是项目特定的 shell 脚本，与 AutoSOTA 的 `config.yaml` 格式、插件系统深度绑定

**潜在风险：**
- 计划声称"直接复用"的代码根本不在参考仓库中。开发者需要从文档描述自行实现 SHA256 保护逻辑，预计需要 2-3 天额外工作量
- 文档描述的三层防御中，前两层是 Claude prompt 级别的软约束（"don't touch eval"），对 AutoAD 的 Agent 不一定有效——如果没有 AutoSOTA 的特定 prompt 体系，这些软约束需要重新设计适配

**需核对：**
- 确认是否已有 AutoSOTA 的 `record_score.sh` 内部实现，还是必须从文档重新实现
- 使用 AutoSOTA 的三层防御时，前两层（prompt 注入）需要针对 AutoAD 的 Agent prompt 模板重新编写，不能简单"复用"

---

### GAP-02：software-agent-sdk `StuckDetector` — 深度耦合 OpenHands 事件类型系统

**计划声称（Plan 06 §3.3）：**
> 直接复制 `openhands-sdk/openhands/sdk/conversation/stuck_detector.py`（320 行，零外部依赖）

**实际情况：**
- `StuckDetector` 确实 320 行，但并非零外部依赖——它依赖 `openhands.events` 中的类型层级：
  - `ActionEvent` / `ObservationEvent` / `AgentErrorEvent` / `MessageEvent` / `CondensationSummaryEvent`
  - `ConversationState.events`（文件回退的大事件列表）
  - `EventSource` 枚举
- `_event_eq()` 方法直接引用这些事件类型的字段（`event.source` / `event.thought` / `event.action` / `event.tool_name` / `event.observation` / `event.error`）
- `_is_stuck_context_window_error()` 方法在源代码中返回 `False`，标注 TODO 指向 GitHub issue #282 ——这部分代码未完成
- 检测算法基于**内存中的事件流**（in-memory `ConversationState.events`），不是基于日志文件或 transcript

**潜在风险：**
- "直接复制"意味着必须同时复制整个 OpenHands 事件类型系统（~10+ 个类、枚举、协议）。不复制则需自行实现兼容的事件模型，开发量远超预期（不仅是 320 行）
- 计划描述的 StuckDetector 与 SWE-Together 的 Sentinel 是两套不同的检测机制（一个基于事件流，一个基于 transcript 文件），但文档未区分两者适用场景
- `_is_stuck_context_window_error` 是未完成的 stub，如果此场景在实际运行中出现，检测会漏报

**需核对：**
- 明确 AutoAD 的 Agent 是否会产生与 OpenHands 兼容的事件流。如果不兼容，重新实现 StuckDetector 需要额外 2-3 天
- 决定使用 event-based（类 OpenHands）还是 transcript-based（类 SWE-Together）的 stuck 检测策略，并据此调整技术方案

---

### GAP-03：SWE-Together Sentinel — 深入耦合 Claude Code / OpenCode Transcript 格式

**计划声称（Plan 04 §5）：**
> 直接复用 SWE-Together 的 `eval_infra_sentinel.py` 设计

**实际情况：**
- Sentinel（930 行）包含两个完全独立的 transcript 解析器：
  - 一个解析 Claude Code 的 JSONL 格式（`{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit"}]}}`）
  - 一个解析 OpenCode 的 JSONL 格式（`{"type":"tool_use","part":{"tool":"edit"}}`）
- 检测器链中的 `no_agent_progress` / `empty_transcript` / `provider_*` 等模式均基于特定的 JSONL 结构模式匹配
- `EMPTY_PATCH_BYTES = 200` 和 `EDIT_TOOL_NAMES` 硬编码了 SWE-Together benchmark 任务的校准值
- Sentinel 是 **post-hoc 分类器**（实验完成后分析产物），不是**运行时守护进程**——与计划描述的 Sentinel 定位不同

**潜在风险：**
- AutoAD 的 Agent trace/transcript 格式必然不同于 Claude Code 或 OpenCode（因为 AutoAD 使用不同的 Agent 框架）。这意味着 sentinel 需要从零实现新的解析器
- 计划将 Sentinel 描述为"运行时监控守护进程"（定时轮询、PID 检查、heartbeat），但 SWE-Together 的 Sentinel 是"实验后分类器"——两者设计意图不同。"直接复用"意味着需要重写 Sentinel 的核心逻辑
- `EMPTY_PATCH_BYTES = 200` 等阈值面向 SWE-bench 编程任务，在 AutoAD 的实验场景下完全不适用（实验脚本修改量通常远大于代码 bugfix）

**需核对：**
- 澄清 Sentinel 定位：是**运行时的守护进程**（如计划 §5.8 描述的轮询 PID/heartbeat）还是**实验后的分类器**（如 SWE-Together）？这决定架构方向完全不同
- 如果定位为运行时守护进程，SWE-Together 的代码不可复用，需重新设计
- 如果定位为实验后分类器，需为 AutoAD 的 Agent trace 格式编写新解析器

---

### GAP-04：MiMo step signature — TypeScript/EffectTS 技术栈不兼容

**计划声称（Plan 06 §3.2）：**
> 直接复用 MiMo `stableStringify()` + `stepSignature()`

**实际情况：**
- MiMo (mimo-code) 使用 **TypeScript + EffectTS + Bun**，核心代码在 `packages/opencode/src/` 中
- `stableStringify` 确实存在且逻辑可独立抽取，但 step signature 检测集成在 `ActorRegistry` 和 `Bus` 事件系统中，不是独立函数
- 卡死检测是一个**后台 fiber**，每 60 秒查询 SQLite 数据库获取 actor 列表，检查 `last_turn_time > 5 分钟`——不是 per-turn 检测
- 预算执行依赖 `AbortSignal.timeout()` 和 `Effect.repeat(Schedule.fixed())`，是 Bun/Effect 特有的并发模型
- 优雅关闭机制（checkpoint writer 使用独立的 token budget）深度依赖 Effect 的 fiber 模型

**潜在风险：**
- `stableStringify()` 本身可以移植（约 30 行），但 `stepSignature()` 的检测逻辑耦合于 Actor 状态机、数据库轮询和事件总线
- 计划描述的 "轻量级 step signature" 忽视了背后的基础设施：多 Actor 的数据库状态跟踪、生命周期管理、后台 fiber 调度
- Python 中缺乏 EffectTS 等效的并发模型，实现同等能力的 actor registry + 后台扫描需要引入 Celery / asyncio 任务系统，增加复杂度

**需核对：**
- 评估是否需要完整的 Actor Registry + 数据库轮询方案（如需多 session 并发监控），或简化为每个 session 内进程级别的 per-turn 检测（后者适合单人开发初期）
- 确认 Python 中的 stuck 检测方案：使用 asyncio 后台任务 + 内存中的 turn 记录，还是引入 SQLite 持久化 actor 状态

---

### GAP-05：claude-code-internals — 逆向工程代码，质量不可靠

**计划声称（多处引用）：**
> 吸收 Claude Code 的设计（上下文管理、工具安全、agent 生命周期）

**实际情况：**
- `claude-code-main/` 是通过反编译得到的源代码，README 明确标注 "~1341 tsc errors from decompilation"
- 所有 30 个 feature flag 被 polyfill 强制返回 `false`，包括：
  - `COORDINATOR_MODE`（多 Agent 协作）—— 本计划的核心架构
  - `KAIROS`（长时间自治 Agent）—— 本计划的 Coordinator
  - `WORKFLOW_SCRIPTS`（用户定义的工作流）
  - `PROACTIVE`（主动执行）
  - `WEB_BROWSER_TOOL`
- 这些子系统虽然代码存在，但全部是禁用的存根，不是可工作的实现
- 上下文压缩策略（`autoCompact.ts` / `reactiveCompact.ts` / `contextCollapse/index.ts`）是独立的 TypeScript 模块，但依赖 React Ink 渲染引擎和 `@anthropic-ai/sdk` 客户端
- Permission 系统（6300 行）的 YOLO 分类器和路径验证规则是针对 Claude Code 的 Shell 工具使用模式训练的，不可直接通用

**潜在风险：**
- 计划参考 Claude Code 的 "多 Agent 协作模式" 和 "自动上下文管理"，但这些功能在反编译代码中被 flag 禁用——可能 Anthropic 也未完全实现或已放弃
- 从有 ~1341 个类型错误的逆向工程代码中提取"最佳实践"，可能在错误的假设上构建架构
- 6300 行的 Permission 系统暗示了工具安全管控的复杂度远超计划预期——计划中对 ExecutorAgent 的安全约束（filesystem 限制到 worktree、shell 白名单）仅是 Claude Code 安全体系的一小部分

**需核对：**
- 明确哪些 Claude Code 设计是**确实可参考的**（如独立工具目录的组织模式），哪些是**被 feature flag 禁用、不可靠的**（如 COORDINATOR_MODE）
- 评估是否需要为 AutoAD 实现类似 Claude Code 的 YOLO 权限分类器（prompt 注入检测），或采用更简化的白名单策略

---

### GAP-06：AI-Scientist — 模板紧耦合的线性流水线，不可模块化复用

**计划声称（多处引用）：**
> 参考 AI-Scientist 的 "I am done" sentinel、实验执行模式

**实际情况：**
- AI-Scientist 不是可复用的模块化框架，而是一个**特定于 LaTeX 论文写作的线性流水线脚本**
- "I am done" sentinel 在源码中是 `if "I am done" in text:` 的字符串匹配（`generate_ideas.py:157`）——极其粗糙，不是可复用的设计
- 代码修改依赖 **Aider** 库，不是自研 Agent（`perform_experiments.py` 调用 `aider.coders.Coder`）
- 无任何测试 / 类型标注 / 错误处理体系
- 实验模板硬编码了 `shutil.copytree()` 路径，无 git 隔离
- 多 idea 并行使用 `multiprocessing.Process` 共享文件系统，无冲突检测
- 整个 `do_idea()` 函数包裹在 `try/except: return False` 中，无细粒度错误分类

**潜在风险：**
- "I am done" 字符串匹配的简洁性反衬出语义退出检测的难度——计划中 Coordinator 的 "I am done" proposal 也面临同样的可靠性问题
- AI-Scientist 的经验表明：即使最简化的实验自动化（generate → edit via Aider → run → write PDF）也需要 600+ 行 Python 和完整的模板依赖。AutoAD 计划的目标（任意 repo + 自研 Agent + GPU 训练 + 复杂有效性验证）复杂度远超 AI-Scientist 的 scope
- 计划中对 "参考 AI-Scientist" 的表述过于笼统，未区分哪些模式可借鉴（循环结构）、哪些不可复用（LaTeX 输出、Aider 依赖）

**需核对：**
- "I am done" 的字符串匹配方案是否够用？如果不是，是否需要设计结构化的退出信号（如 `StopProposal` schema + coordinator 签名）？
- 实验执行是否考虑复用 Aider 的 `Coder` 作为 ExecutorAgent 的一部分？Aider 已有成熟的 SEARCH/REPLACE 和 edit 策略栈

---

## 第二类：参考项目中存在但计划未吸收的模式

### GAP-07：Arbor 的 EventBus — 计划仅有 JSONL 日志，无事件驱动架构

**参考项目实际情况：**
Arbor 实现了类型化 EventBus 系统，包含：
- 命名事件类型：`IDEA_PROPOSED` / `IDEA_COMPLETED` / `IDEA_MERGED` / `CHECKPOINT_SAVED` / `TREE_MODIFIED` / `AGENT_CRASHED` / `BUDGET_WARNING`
- 事件订阅者模式：Sentinel、Monitor、Checkpointer 等组件作为订阅者注册
- 事件携带结构化 payload

**计划当前设计：**
- `events.jsonl` 作为文件日志，无事件路由，无订阅者模式
- BatchSupervisor 通过"读取 EventStore" 获取事件——轮询而非推送

**差距影响：**
- 无事件驱动架构意味着：StuckDetector 需要轮询文件，Monitor 需要轮询文件，Coordinator 需要轮询文件——N 个组件各自轮询，既有性能浪费又有不一致风险
- 事件驱动架构对单人开发的价值：不需要手动协调各组件间的数据同步，降低心智负担
- 如果后续需要实时 WebSocket 通知或前端状态展示，无事件系统需要较大重构

**建议补充：**
- 评估在第一版中引入轻量级 EventBus（Python `asyncio.Queue` + 通道模式，无需消息队列中间件）
- 至少定义核心事件类型：`ATTEMPT_COMPLETED` / `IDEA_UPDATED` / `BUDGET_EXCEEDED` / `SENTINEL_ALERT` / `COORDINATOR_CYCLE_DONE`

---

### GAP-08：Arbor 的背景 Agent 并发 — 计划完全采用串行流水线

**参考项目实际情况：**
Arbor 的 Coordinator 在运行主循环的同时，可以启动**后台 SearchAgent** 作为 fiber 并发执行文献/代码搜索，结果在下一周期合并。

**计划当前设计：**
- 所有操作严格串行：OBSERVE → IDEATE → SELECT → EXECUTOR → RESULT → REFLECTION → next cycle
- 即使是 IdeaExplorerAgent 也是同步调用的，"按需提供"

**差距影响：**
- 实验运行期间（GPU 训练可能需要数小时），Coordinator 处于空闲等待状态。如果有后台搜索能力，可以在等待期间预研下一轮假设，大幅提升整体效率
- 这一点在 Plan 02 §5 的 "cheap experiment / expensive experiment" 策略中已有暗示，但未落地为并发设计
- 单人开发 + 长 GPU 训练的场景下，串行等待意味着开发者每天只能迭代很少轮次

**建议补充：**
- 定义等待 GPU 训练时的 Coordinator "背景研究"模式：允许 Coordinator 进行低成本的文献/代码搜索，但不修改 Idea Tree
- 明确并发模型（asyncio 协程 / 多线程 / 多进程）和共享数据的同步策略

---

### GAP-09：AutoSOTA 的 Debug Recovery Loop — 计划缺少失败实验的自动修复循环

**参考项目实际情况：**
AutoSOTA 的 `config.yaml` 中包含：
```yaml
max_debug_attempts: 3
max_debug_minutes: 15
```
失败的实验自动进入 debug 循环：输出 → Agent 分析失败原因 → 修复 → 重试，直到达到次数或时间上限。

**计划当前设计：**
- Plan 03 §3.4.4 定义了 ExecutorAgent 的有界修复（最多 3 次），但限于代码层面的 syntax/import/shape 错误
- 对于**实验运行失败**（OOM、配置错误、数据集加载失败），计划走 Sentinel → BatchSupervisor → Coordinator decision boundary 的路径，没有独立的自动修复循环
- 没有 `max_debug_attempts` 或 `max_debug_minutes` 的预算概念

**差距影响：**
- 实验运行失败是**高频事件**（尤其在 DL 训练中：OOM、NaN、配置路径错误、CUDA 版本不匹配）。没有自动修复循环，每个失败都要走完整的 Coordinator cycle，浪费 LLM 调用次数和认知预算
- 单人开发调试期间，实验失败率更高，调试循环的价值更大

**建议补充：**
- 新增 `ExperimentDebugger`：对 Sentinel 分类为 RUN_FAILED 的实验，运行独立的 debug 循环（有限次尝试 + 有限时间预算），不经过 Coordinator
- 定义 debug 循环的预算（max_debug_attempts / max_debug_minutes），与 cognitive budget 分离
- debug 成功 → 重新提交 Job；debug 失败 → 转为 Attempt FAILED，通知 Coordinator

---

### GAP-10：Claude Code / OpenCode 的状态持久化 — 计划缺少 Agent 生命周期管理

**参考项目实际情况：**
- **Claude Code**：使用 `session.json` 跟踪会话状态，包含 tool call 历史、权限状态、上下文窗口水位
- **OpenCode (MiMo)**：使用 SQLite + Drizzle 的 `ActorRegistry` 管理所有 Agent 实体的生命周期，包含字段：
  - `status`（pending / running / completing / completed / failed）
  - `turn_count` / `last_turn_time`
  - `mode`（spawn mode）
  - `context_mode` / `context_watermark`
  - `lifecycle` 状态
  - `background` 标志

**计划当前设计：**
- Session 有定义，但 Agent 级的状态（Coordinator 的 turn 数、当前 cycle 阶段、已用 budget）没有独立的状态表
- Agent 崩溃后恢复全靠 checkpoint 文件 + Idea Tree 重建，没有轻量级的状态心跳

**差距影响：**
- 无法回答"Coordinator 当前正在做什么"——是在等 GPU 还是在思考？卡在第几轮？已用多少 token？
- 缺乏 Agent 级状态追踪，StuckDetector 无法区分"agent 正常思考中"和"agent 卡住了"
- 单人调试时，开发者需要跨多个日志文件拼凑 Agent 的当前状态

**建议补充：**
- 为 Persistent Agent（Coordinator）设计轻量级 AgentStatus 表：`current_phase`、`turn_count`、`last_activity_at`、`cognitive_budget_remaining`、`context_watermark`
- AgentStatus 由 Agent 自身写心跳（每轮 cycle 结束更新），Sentinel 和 StuckDetector 直接读取

---

### GAP-11：系统提示词的版本化和效果追踪 — 参考项目的行业标准未被吸收

**参考项目实际情况：**
- **academic-research-skills**：使用 `.md` 文件管理所有 agent skill prompts，跟随 git 做版本管理
- **Claude Code**：`prompt.ts` 定义了完整的系统提示词生成逻辑，包含 CLAUDE.md 项目指令注入，所有变更发生在 git 版本控制中
- **OpenCode (MiMo)**：系统提示词和 tool 定义在 TypeScript 代码中，版本化函数级别

**计划当前设计：**
- Plan 06 §5 提到 prompt overlay 的版本管理，但仅限"策略变更"产生的 overlay
- 基础系统提示词（Coordinator、Executor、ReflectionAgent 等的初始 prompt）无版本管理
- 无 prompt 变更与评测结果的关联方案

**差距影响：**
- Agent 系统中 prompt 迭代是最频繁的变更之一（通常超过代码变更频率）
- 无可追溯的 prompt 版本历史，单人开发者将在 2-3 周后无法回忆"为什么这个 prompt 是这样写的"
- AI 编程助手修改 prompt 时可能无意中"优化"掉关键的安全约束或行为限制

**建议补充：**
- 每个 Agent 角色的 system prompt 作为独立 `.md` 文件受 git 版本管理，命名规范：`prompts/coordinator/v1.md`、`prompts/executor/v2.md`
- prompt 文件的版本号与评测结果关联：每次 prompt 变更后运行基线评测，结果记录在 `prompts/coordinator/eval_log.jsonl`
- 在 AI 编程助手的项目上下文中固化 prompt 文件列表，防止"发明"新 prompt

---

### GAP-12：Multi-LLM Backend 和 API 降级 — 供应商单一故障点

**参考项目实际情况：**
- **Claude Code**：支持 4 个 API 后端（Anthropic 直连 / AWS Bedrock / Google Vertex / Azure Foundry），且可在运行时切换
- **AutoSOTA**：明确支持 Claude + GPT 双模型后端，`optimize_prompt.md` 中包含针对不同模型的后备策略

**计划当前设计：**
- 只字未提 LLM 供应商选择和降级策略
- `create_deep_agent()` 使用的 LLM 后端未定义
- 无 API 调用失败（限流、超时、服务不可用）的处理方案

**差距影响：**
- 单一 LLM 供应商出现服务故障时，整个实验系统不可用
- 不同模型在不同任务上的效果差异显著（例如 Claude 在代码修改上优于 GPT，GPT 在结构化输出上可能更稳定），没有兜底方案意味着被锁定在一家供应商上
- 单个 API Key 的配额限制可能成为系统瓶颈

**建议补充：**
- 在 `AgentTaskSpec` 中增加 `model_profile` 的备选列表：首选模型失败时自动降级到次选模型
- 定义 API 限流策略的退避算法（指数退避 + 最大重试次数 + 熔断）
- 至少支持 2 家 LLM 供应商，避免单一故障点

---

### GAP-13：实验产物 / Checkpoint 的 TTL 和自动清理策略

**参考项目实际情况：**
- **AI-Scientist**：每次实验在临时目录运行，成功后将结果复制到最终目录，失败则丢弃
- **OpenCode (MiMo)**：checkpoint 有独立的 TTL，过期后由后台任务回收

**计划当前设计：**
- Artifact 目录结构丰富，但无存储清理策略
- 10+ 轮实验 × 多个 seed × 多个 checkpoint → 磁盘消耗可能快速达到 TB 级别
- 无产物过期时间、无自动清理规则

**差距影响：**
- DL 实验的 checkpoint 通常数百 MB 到数 GB。20 轮尝试 × 5 个 seed = 100+ 个 checkpoint，磁盘压力巨大
- GPU 服务器存储空间有限，溢出会导致训练失败
- 单人开发者不会主动清理旧产物，问题只会恶化

**建议补充：**
- 定义产物生命周期策略：`attempt TTL = 7 天`、`checkpoint TTL = 30 天`（champion 的 checkpoint 永久保留）
- 后台清理任务：定期扫描 attempt 和 checkpoint 目录，清除过期产物
- 策略可配置 + 安全警告（删除前记录日志）

---

## 第三类：计划中存在的隐性假设与实际不符

### GAP-14：假设参考代码可"直接复制使用"——实际每个参考项目都需要适配

**隐性假设**：计划多处使用"直接复用"、"直接复制"、"直接吸收"，假设参考项目代码可以即插即用。

**实际情况**：以上 GAP-01 到 GAP-06 已充分证明，每个参考项目都需要 2-5 天的适配改造才能用于 AutoAD 的上下文。

**汇总适配工作估算（仅已验证的 6 项）：**

| 参考项目 | 声称可复用 | 实际状态 | 适配工作量（单人不含 AI 辅助） |
|----------|-----------|----------|------------------------------|
| AutoSOTA `record_score.sh` | 直接复用 | 文件不存在 | 2-3 天重新实现 |
| software-agent-sdk `StuckDetector` | 直接复制（320 行） | 需同时复制事件类型系统 | 3-5 天适配 |
| SWE-Together Sentinel | 直接复用设计 | 需重写 transcript 解析器 | 3-4 天 |
| MiMo `stableStringify` | 直接复用 | 可移植但后台检测需重做 | 1 天（函数）+ 3 天（后台） |
| claude-code-internals | 吸收设计 | 逆向工程存根，核心功能被禁 | 信息参考，不可直接复用 |
| AI-Scientist 实验循环 | 参考模式 | 不可模块化的线性流水线 | 提供思路参考 |

**合计最低额外工作：14-18 天（约 3-4 周）**，且这些适配工作必须在功能开发前完成，因为它们被计划视为"已有基础"。

**需核对：**
- 重新评估"直接复用"策略是否应调整为"参考设计 + 自行实现"，将适配工作显式纳入排期
- 优先级的建议：StuckDetector（3-5 天）→ SHA256 Guard（2-3 天）→ Sentinel（3-4 天）→ EventBus（可选）

---

### GAP-15：假设参考代码无质量瑕疵——实际多个参考代码存在完成度问题

**计划隐性假设**：参考项目代码是高质量、可直接复用的生产代码。

**实际情况：**
- **AI-Scientist**：无测试、无类型标注、TODO 注释随处可见、`try/except: return False` 的粗粒度错误处理
- **software-agent-sdk StuckDetector**：`_is_stuck_context_window_error` 返回 `False`（stub，标注 GitHub issue #282）
- **SWE-Together Sentinel**：硬编码 `EMPTY_PATCH_BYTES = 200` 和 `EDIT_TOOL_NAMES`，无校准机制
- **claude-code-internals**：1341 个 TypeScript 类型错误，所有高级功能被 flag 禁用

**需核对：**
- 确定是否有质量门槛——哪些参考代码可以直接使用其"弱"实现（如 AI-Scientist 的 `try/except` 级别的错误处理），哪些需要重写
- 对 StuckDetector 的 `_is_stuck_context_window_error` stub 需要自行实现完整方案

---

## 参考项目综合启示：计划可优化的方向汇总

综合以上 15 项差距分析，从参考项目中提炼以下可立即采纳的优化建议：

### 高价值补充（建议纳入第一版边界）

| 优化点 | 参考来源 | 说明 |
|--------|----------|------|
| Debug Recovery Loop | AutoSOTA | 实验运行失败后自动修复循环（max_debug_attempts + max_debug_minutes），减少不必要的 Coordinator cycle |
| Agent 级状态心跳 | OpenCode (MiMo) / Claude Code | 为 Persistent Agent（Coordinator）设计轻量级 AgentStatus 表，支持 StuckDetector 实时判断 |
| LLM 多供应商备选 | Claude Code / AutoSOTA | 至少支持 2 家 LLM 供应商，定义降级策略 |
| 产物自动清理策略 | AI-Scientist / OpenCode | 定义 checkpoint/attempt TTL，后台定期清理 |
| Aider 作为 Executor 基座 | AI-Scientist | 评估复用 Aider 的成熟 edit 栈替代自研 SEARCH/REPLACE，降低 AI 编程助手生成代码的安全风险 |

### 中期补充（第二版或后续迭代）

| 优化点 | 参考来源 | 说明 |
|--------|----------|------|
| 类型化 EventBus | Arbor | 替代 JSONL 轮询，事件驱动架构 |
| 背景 Agent 并发 | Arbor | GPU 训练等待期间进行低成本研究 |
| 30 Feature Flag 体系 | Claude Code | 支持 A/B 测试和渐进式功能上线 |
| MCP 协议支持 | Claude Code | 与外部工具服务标准化通信 |

### 负面教训（应避免的设计）

| 教训 | 来源 | 说明 |
|------|------|------|
| 不要将 Agent 循环完全依赖 prompt | Arbor | Arbor 的 6 步循环在 prompt 中定义，无代码强制执行——LLM 偏航时无安全网。AutoAD 的 Coordinator cycle 建议在代码层加入 cycle step 状态机作为安全护栏 |
| 不要用字符串匹配做语义退出检测 | AI-Scientist | `"I am done" in text` 过于脆弱。结构化退出信号更可靠 |
| 不要低估 transcript 格式差异 | SWE-Together | 不同 Agent 的 trace 格式天差地别，解析器不可复用。建议提前定义 AutoAD 的 Agent trace schema |

---

## 总结：从参考项目看计划的核心短板

**最大疏漏：计划高估了"直接复用"的可行性，低估了参考代码实际使用前的适配开销。** 6 个声称可直接复用的引用中，5 个需要 2-5 天改造才能投入使用。合计额外 3-4 周的适配工作未被计划纳入。

**第二大疏漏：参考项目中已有成熟经验未被吸收。** Debug Recovery Loop、Agent 级状态心跳、产物生命周期管理、EventBus、背景搜索并发——这些在参考项目中已验证的模式可以显著提升系统的健壮性和开发效率，但计划完全未提及。

**第三大疏漏：多个参考代码本身存在质量问题。** 将未完成的 stub（stuck detector 的 context window check）、1341 个类型错误的逆向工程、无测试的线性流水线脚本作为"直接复用"目标，开发者在实际编码时会反复碰壁。

**建议行动：**
1. 逐项重新评估"直接复用"清单，标注实际适配工作量
2. 将适配工作单独列为开发计划的独立 PR（而非假设为"已有基础"）
3. 优先吸收 Debug Recovery Loop 和 Agent 状态心跳两个高价值模式
4. 建立参考代码的质量门槛——哪些可直接用、哪些需重写、哪些仅参考思路
