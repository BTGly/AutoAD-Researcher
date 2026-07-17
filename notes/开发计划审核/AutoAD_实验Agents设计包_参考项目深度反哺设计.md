# AutoAD 实验 Agents 设计包 — 参考项目深度反哺设计

> 审核对象：`notes/后端开发/AutoAD_实验Agents设计包/`  
> 深度参考：Arbor / AutoScientists / AutoSOTA / SWE-Together / aider / DeepAgents / OpenCode(MIMO) / mini-swe-agent / AI-Scientist / autolab / autoresearch  
> 日期：2026-07-16  
> 目标：将成熟项目的设计模式反哺到当前开发计划中，而非仅做验收式审核

---

## 反哺设计总览

以下按模块分类，每条设计建议标注来源项目、当前计划缺失内容、以及具体可落地的代码级实施方案。

---

## 一、Agent 框架层（源自 DeepAgents / OpenCode / MIMO）

当前计划的 CognitiveTaskRunner 是一个薄接口（Python Protocol），但缺少了 DeepAgents 框架中已被验证为必须的若干模式。

### R1. 采纳 DeepAgents 的 Middleware 栈架构

| 维度 | 内容 |
|------|------|
| **来源** | DeepAgents `graph.py` lines 750-834 |
| **当前计划** | `04_ExecutorAgent.md` 中 ExecutorAgent 有工具和权限配置，但无中间件概念。每个 Agent 的工具、权限、上下文管理、中断逻辑全部硬编码在 Agent 配置中。 |
| **DeepAgents 的做法** | 13 个中间件按固定顺序组成栈，每个中间件通过 `before_agent` / `wrap_model_call` / `after_agent` 三个 hook 拦截 Agent 执行流。关键中间件：TodoList → Skills → Filesystem → SubAgent → Summarization → PatchToolCalls → AsyncSubAgent → 用户中间件 → ToolExclusion → PromptCaching → Memory → HumanInTheLoop。中间件可插拔可排序。 |
| **可落地的具体方案** | 在 `CognitiveTaskRunner` 下增加一个轻量 Middleware 接口，第一版先实现 4 个核心中间件：`PermissionMiddleware`（工具权限校验）、`SummarizationMiddleware`（上下文压缩）、`BudgetMiddleware`（token/cost/wall 限制）、`TraceMiddleware`（事件追踪）。每个 Agent 创建时通过 middleware list 配置。后续可增量添加。 |

### R2. 采纳 DeepAgents 的 System Prompt 四段组装

| 维度 | 内容 |
|------|------|
| **来源** | DeepAgents `graph.py` system prompt 组装部分 |
| **当前计划** | Agent 的 system prompt 是单一字符串，不同角色的 prompt 独立维护。StrategyOverlay 以 `prompt_changes.jsonl` 追加修改，无结构化组装。 |
| **DeepAgents 的做法** | system prompt 由 4 个命名段组成：**USER**（create_deep_agent 的 system_prompt 参数）、**BASE**（默认 agent prompt 常量）、**CUSTOM**（HarnessProfile 替换 BASE）、**SUFFIX**（HarnessProfile 追加）。用 `\n\n` 拼接。支持 `SystemMessage` content blocks 保留。 |
| **可落地的具体方案** | 每个 Agent 的 prompt 改为 4 段拼接：`role_prompt`（角色定义） + `policy_prompt`（策略/overlay） + `tools_prompt`（工具定义） + `context_prompt`（本次调用的上下文）。各段独立版本化、可覆盖。StrategyOverlay 修改 `policy_prompt` 段即可，不影响其他段。 |

### R3. 实现「成本追踪 + 三步预算检查」模式（源自 mini-swe-agent）

| 维度 | 内容 |
|------|------|
| **来源** | mini-swe-agent `default.py` lines 107-133 |
| **当前计划** | `01_大框架.md` 第 13 节定义了 CognitiveBudget 的四个硬限制字段，但只提了检查时机是「每次 query 前」，没有具体的检查顺序、错误类型、以及超出后的行为。 |
| **mini-swe-agent 的做法** | `query()` 方法按固定顺序执行三步检查：① `step_limit` + `cost_limit` 联合检查（超出则 `raise LimitsExceeded`）→ ② `wall_time_limit` 检查（超出则 `raise TimeExceeded`）→ ③ 自增 `n_calls` → ④ 调用模型。两步检查生成不同的异常类型，上层可区分处理。此外还有全局级 `GlobalModelStats` + 环境变量 `MSWEA_GLOBAL_COST_LIMIT`，跨 Session 兜底。 |
| **可落地的具体方案** | 在 `CognitiveTaskRunner.invoke()` 中按固定顺序：① step/call count 检查 → ② accumulated cost 检查 → ③ wall time 检查 → ④ 实际 LLM 调用 → ⑤ 更新计费。超出分别抛 `StepBudgetExceeded` / `CostBudgetExceeded` / `TimeBudgetExceeded`。增加全局预算（从 Session 级别提升到 Run 级别），通过环境变量或配置设定。 |

### R4. 实现 Agent 输出的 JSON 恢复与截断恢复（源自 Arbor）

| 维度 | 内容 |
|------|------|
| **来源** | Arbor `_agent_recover.py` lines 78-93 和 `agent.py` lines 378-397 |
| **当前计划** | 所有 Agent 依赖结构化 JSON 输出，但无解析失败恢复。 |
| **Arbor 的做法** | `recover_json()`：先尝试 `json.loads()` 直接解析，失败后扫描 `assistant_texts` 反向查找最后一个可解析的 JSON。`_extract_json_block()` 处理代码 fence 包裹和不平衡括号。max_tokens 截断时：发送 "Please continue exactly where you left off" 最多 3 次。 |
| **可落地的具体方案** | 为所有 Agent 的 structured output 增加 `JsonRecoveryMiddleware`：① 优先 `json.loads()`；② 失败则 `recover_json()` 反向扫描；③ 还失败则 retry 1 次加 instruct（"输出必须是纯 JSON"）；④ 最终降级为 free-text + 人工标记。max_tokens 截断场景：注入 continue prompt。 |

### R5. 采纳 OpenCode/MIMO 的 Permission 三层架构

| 维度 | 内容 |
|------|------|
| **来源** | MIMO `permission/index.ts`、OpenCode Go `permission/permission.go` |
| **当前计划** | `04_ExecutorAgent` 的 PreApplyPatchGate / PostApplyDiffGuard 是硬编码检查，不是通用的权限系统。 |
| **MIMO 的做法** | 三层权限：① **ConfigPermission**（通配符 pattern + action 映射，持久化规则）；② **Session-scoped approval**（"always" 创建持久 rule）；③ **Ask mode**（实时询问）。System-spawned agents 自动 `interactive: false`，ask 模式等同于 deny。权限错误明确区分为 `DeniedError` / `RejectedError` / `CorrectedError`。 |
| **可落地的具体方案** | 在 `PermissionMiddleware` 中实现通用权限引擎：每条规则 = `(pattern, action)`，pattern 支持通配符，action ∈ `allow/deny/ask`。工具调用前按规则列表顺序评估，first match wins。核心路径（写 protected paths、shell 执行、网络访问）必须有明确规则，不依赖隐式 allow。 |

### R6. 采纳 MIMO 的 DoomLoop 检测（重复步骤）

| 维度 | 内容 |
|------|------|
| **来源** | MIMO `processor.ts` lines 376-422 |
| **当前计划** | `07_收敛.md` 第 3 节 StuckDetector 提到了 lightweight step signature + 5-mode detector，但设计粒度较粗。 |
| **MIMO 的做法** | `DOOM_LOOP_THRESHOLD = 3`：取当前 assistant 消息的最后 3 个 parts，检查是否同一 tool + 同一 input（JSON 序列化比较）。若匹配则触发 doom_loop permission ask。System-spawned agents 非交互模式下直接 `DeniedError`。同时有 repeated-step detection 在 prompt 层面注入 `<system-reminder>`。 |
| **可落地的具体方案** | StuckDetector 的 Step Signature 部分直接复用 MIMO 的 `stableStringify` + `stepSignature` 算法。检测到重复后：① 先注入 nudge（"步骤重复，请减少相同操作"）；② 连续超限后触发 `stuck` 事件。Threshold 可配置（默认 3）。 |

---

## 二、系统安全与完整性层（源自 autolab）

### R7. 采纳 autolab 的 14 层正确性门禁级联

| 维度 | 内容 |
|------|------|
| **来源** | autolab 各 `tests/test.sh` 和 `verify_*.py` 文件 |
| **当前计划** | `06_实验有效性.md` 第 2.1 节只有三层 SHA256 防御：prompt 警告 + 后果警告 + hash 检查。 |
| **autolab 的做法** | 14 种检查类型按固定顺序级联：① Build File Integrity (SHA256) → ② Protected File Tamper → ③ /app Whitelist → ④ Banned Library/API 正则 → ⑤ Syntax Compile → ⑥ Small Correctness → ⑦ Quality Gate (MRR/Recall threshold) → ⑧ Must-Beat-Baseline → ⑨ Output Existence → ⑩ Benchmark Correct → ⑪ Exact Output Equality → ⑫ Test Split Isolation → ⑬ Task.toml Drift → ⑭ Hidden Seed Verification。任何一级失败立即返回 score 0，不继续。 |
| **可落地的具体方案** | 在 EvaluationContract 中实现 **ValidityGateChain**：按顺序定义一组 gate，每个 gate 有 `check() → Pass/Fail`。当前第一版至少实现：① SHA256 Integrity Gate → ② Allowed Paths Gate → ③ Syntax Check Gate → ④ Smoke Test Gate → ⑤ Must-Beat-Baseline Gate。每个 gate 的 fail 原因结构化记录。 |

### R8. 实现 Hidden Seed / 测试数据隔离

| 维度 | 内容 |
|------|------|
| **来源** | autolab `moving_mnist_world_model/tests/test.sh` lines 31-49、autolab `agent_tool_routing/tests/verify_and_benchmark.py` lines 132-141 |
| **当前计划** | `06_实验有效性.md` B_dev/B_test 设计正确，但未讨论测试数据生成的时间点和校验机制。 |
| **autolab 的做法** | B_test 数据在 verify 时从 hidden seed 生成（只有 verifier 代码知道的 seed），训练容器中不存在 test 数据。`moving_mnist` 检查 `/data/moving_mnist/test.pt` 是否存在（agent cache 污染防御）。`agent_tool_routing` 使用环境变量 `TOOL_ROUTER_SECRET_SEED` 作为 hidden seed。 |
| **可落地的具体方案** | 在 EvaluationContract 中明确 B_test 数据的生成策略：① test 数据不在环境准备阶段生成，只在 verify 时由 hidden seed 生成；② attempt 目录和 worktree 中不允许出现 test 数据；③ 训练容器中 `B_test` split 不存在。 |

---

## 三、研究决策层（源自 AutoScientists / AutoSOTA）

### R9. 采纳 AutoScientists 的 Idea 多样性守卫

| 维度 | 内容 |
|------|------|
| **来源** | AutoScientists `ROLE-ANALYST.md` Step 4a (lines 1182-1230) |
| **当前计划** | Coordinator 在 Compact Cycle 后直接输出 CycleDecision，无多样性约束。 |
| **AutoScientists 的做法** | 分析师提交 proposal 前的 4 项检查：① **方向多样性**（最近 3 个 proposal 共享同一 axis+direction 必须换方向）；② **假设多样性**（两个 proposal 不能同 axis）；③ **失败范围检查**（proposal 不能在已 DISCARD 范围内）；④ **野心配额**（至少 1 个 proposal 是 bold 类型）。 |
| **可落地的具体方案** | 在 Coordinator 的 CycleDecision 校验中增加 `DiversityGuard`：① 检索 Idea Tree 中最近 N 个已执行节点的新旧程度和 axis 分布；② 新 idea 的 axis 与最近 3 个已执行 idea 的 axis 重叠度超过阈值则 reject；③ 连续 N 个同一 axis 的 idea 后强制换轴；④ 维护 dead_ends 列表（已 DISCARD 的干预），新 idea 不能落在其范围内。 |

### R10. 采纳 AutoSOTA 的「红线自检」与蜜月期

| 维度 | 内容 |
|------|------|
| **来源** | AutoSOTA `optimize_prompt.md` lines 282-308（R1-R6 自检）和 lines 629-667（蜜月期） |
| **当前计划** | 无 idea 提案前的自检机制。DecisionEngine 对结构变更和参数调优不做区分。 |
| **AutoSOTA 的做法** | **红线自检**：每次 idea 选定前依次检查 R1-R6（评估指标参数、评估脚本、硬编码输出、牺牲其他指标、数据泄露、修改数据集），任何一条违反标记为 `REJECTED`。**蜜月期**：LEAP 迭代（结构变更）后启动 5 轮蜜月，期间允许参数调优，5 轮内任一达到 new best 则 LEAP 视为成功。 |
| **可落地的具体方案** | ① 在 CycleDecision 校验中增加 `RedLineCheck`：检查 idea 是否涉及修改 protected paths、eval script、metric、data split——对应 R1-R6 的简化版。② 在 Idea Node 中增加 `category` 字段（`param_tune` / `code_change` / `architecture_change`），`architecture_change` 类型启动蜜月期（默认 N=3 轮），蜜月期内不因单轮无提升而剪枝。 |

### R11. 采纳 AutoScientists 的分布式停滞检测

| 维度 | 内容 |
|------|------|
| **来源** | AutoScientists `ROLE-ANALYST.md` Step 0.2 |
| **当前计划** | `07_收敛.md` 第 3 节 ConvergenceMonitor 是中心化计算。 |
| **AutoScientists 的做法** | 分析师分散检测：① `rotations_since_keep` 触发：3+ 轮无 KEEP → 发起 DISCUSSION-TRIGGER；② `single-axis-exhaustion` 触发：最后 8+ 个 DISCARD 集中在 ≤3 个 axis 且无配对 probe → 发起 DISCUSSION-TRIGGER。不依赖中心监控。 |
| **可落地的具体方案** | 在 Coordinator 的每个 decision boundary 中增加本地停滞检测：① 读取最近 N 个 CognitiveCommit 的 verdict 分布；② 若连续 N 轮无 KEEP/IMPROVEMENT → 标记 stagnation flag 给 ConvergenceMonitor；③ 若最近 M 个 idea 集中在同一 research axis 且都 NOT_SUPPORTED → 标记 axis exhaustion。这些检测在 LLM 调用前执行（0 token 成本）。 |

### R12. 采纳 AutoScientists 的冠军竞争检测

| 维度 | 内容 |
|------|------|
| **来源** | AutoScientists `ROLE-GPU.md` Step 5 (lines 637-676) |
| **当前计划** | `06_实验有效性.md` 第 7 节 DecisionEngine 的 champion promotion 无竞争处理。 |
| **AutoScientists 的做法** | 记录结果前：① 重新读取 champion 版本号；② 若冠军在训练期间变更（`race_condition = True`），针对当前冠军重新评估 delta，而非训练前的旧冠军。champion 写入用 `tmp.replace(dst)` 原子操作。 |
| **可落地的具体方案** | ChampionStore 增加 `revision` 版本号，晋升操作要求传入预期的版本号（乐观锁）。若版本不匹配 → 重新计算 delta。晋升操作用原子写入（tmp file + `os.replace()`）。Attempt 完成后先读当前 champion 再决策。 |

---

## 四、实验执行层（源自 AI-Scientist / autolab / autoresearch）

### R13. 采用 AI-Scientist 的「固定时间预算 + git 分支前沿追踪」模式

| 维度 | 内容 |
|------|------|
| **来源** | autoresearch `program.md` + `train.py`（TIME_BUDGET=300s） |
| **当前计划** | `05_ExperimentJob.md` 有 timeout 机制，但实验长度是自由设定的。 |
| **autoresearch 的做法** | 每次训练固定 300 秒（除去编译预热），学习率调度基于 `progress = training_time / TIME_BUDGET`。整个实验历史通过 git branch 的前沿（frontier）隐式追踪——每次保留的提交就是一次改进。`results.tsv` 记录全部实验日志（包括丢弃的），不在 git 追踪中。 |
| **可落地的具体方案** | 对**探索型实验**（cheap experiment）实施固定时间预算：Attempt 的 LaunchProfile 增加 `time_budget_sec` 字段，训练脚本内部按进度比例调度学习率，时间到自动停止。实验日志写入 `results.tsv`（不受 git 追踪），Idea Tree 只追踪保留的提交。两种实验模式：`fixed_budget`（autoresearch 模式，固定时间）和 `unlimited`（Arbor 模式，到收敛为止）。 |

### R14. 采纳 AI-Scientist 的日志解析式指标提取

| 维度 | 内容 |
|------|------|
| **来源** | AI-Scientist `perform_experiments.py` 和 autoresearch `program.md` 的 grep 模式 |
| **当前计划** | `05_ExperimentJob.md` 第 4.2 节的 heartbeat 要求训练适配器输出结构化的 status/epoch/step/loss/best_metric，但「当训练脚本无法修改」时的降级方案只有 process heartbeat。 |
| **AI-Scientist 的做法** | 阅读 stdout/stderr：运行 `grep "^val_bpb:" run.log` 提取关键指标。整个实验循环全靠日志解析驱动——训练脚本不需要输出结构化数据。 |
| **可落地的具体方案** | 实现可配置的 `LogMetricExtractor`：一组正则模板，从 stdout/stderr 中提取 loss、metric、epoch、step。第一版预置 PyTorch Lightning 格式、bare print 格式、wandb 格式的正则。可在 EvaluationContract 中自定义 extractor。日志提取结果写入 metrics.json，供 OutcomeCard 使用。 |

### R15. 采纳 autolab 的锚定评分（Anchored Scoring）

| 维度 | 内容 |
|------|------|
| **来源** | autolab `tests/test.sh` 中的 scoring formula |
| **当前计划** | `06_实验有效性.md` 第 7 节 DecisionEngine 的规则是布尔门禁（promote/reject），没有连续的得分函数。 |
| **autolab 的做法** | 评分公式：`reward = clip(0.5 * log(speedup) / log(ref_speedup), 0, 1)`（系统优化）或 `reward = clip((agent - baseline) / (reference - baseline), 0, 1)`（模型开发）。评分有明确的上界（1.0）和下界（0.0），且基于 baseline 和 reference 两个锚点。 |
| **可落地的具体方案** | Champion 晋升时计算连续得分：`score = (new_metric - baseline_metric) / (reference_metric - baseline_metric)`，clip 到 [0, 1]。baseline 来自 initial evaluation，reference 来自已知最优或 theoretical maximum。此得分用于：① champion 排序（如果多个 candidate）；② ConvergenceMonitor 计算 improvement velocity；③ 判断是否接近 reference（early stop 条件）。 |

---

## 五、运维与监控层（源自 SWE-Together / Arbor）

### R16. 采纳 SWE-Together 的 Gating Predicate + 排除式计分

| 维度 | 内容 |
|------|------|
| **来源** | SWE-Together `eval_infra_sentinel.py` line 491 和 `eval/run_eval.py` `_effective_judge_score()` |
| **当前计划** | `05_ExperimentJob.md` 第 5.6 节有 `run_failed` 排除式计分，但无 Gating Predicate。 |
| **SWE-Together 的做法** | **Gating predicate**：patch > 200 bytes → 即使 stderr 有错误也算 `ok`，不重跑。**排除式计分**：`run_failed` 的 attempt 不计为 0.0，而是从评分中排除（返回 None）。 |
| **可落地的具体方案** | 在 Sentinel 的结果分类中增加 gating predicate：① 若 `patch.diff` > 200 bytes + `metrics.json` 存在 → 即使 stderr 有错误也标记为 `COMPLETED`；② `run_failed` 的 attempt 在 champion ranking 和 convergence 计算中排除（不参与 KEEP/DISCARD 判定）。 |

### R17. 采纳 Arbor 的事件总线设计

| 维度 | 内容 |
|------|------|
| **来源** | Arbor `src/events/` 目录（typed EventBus） |
| **当前计划** | 多处写 Event 但无统一 schema。 |
| **Arbor 的做法** | 全系统使用 typed EventBus：所有事件（`LLM_CALL`、`CACHE_STAT`、`HEARTBEAT`、`CYCLE_START`、`CONVERGENCE_REACHED` 等）通过统一总线发送，事件具有 `event_id`、`event_type`、`source`、`timestamp`、`payload` 字段。支持订阅者模式。 |
| **可落地的具体方案** | 定义统一事件基类 `ExperimentEvent`：`event_id: UUID`、`event_type: str`、`timestamp: datetime`、`source: str`、`session_id: str`、`payload: dict`。所有组件通过单例 EventBus 发送事件。第一版不实现订阅者模式，直接写 `events.jsonl`。事件 schema 在 `experiment/events.py` 中统一定义。 |

### R18. 采纳 SWE-Together 的 Sidecar 模式 + skip-existing

| 维度 | 内容 |
|------|------|
| **来源** | SWE-Together `trial_infra.json` sidecar 和 `is_task_completed()` |
| **当前计划** | idempotency key 用于 Job/Attempt 去重，但无 sidecar 缓存机制。 |
| **SWE-Together 的做法** | 每个 trial 完成后写 `trial_infra.json`（sidecar 文件）。`classify_or_load()` 优先读缓存避免重复计算。`is_task_completed()` 检查 existing trial + infra 结果，infra_failed 的 trial 需要重跑（不 skip）。 |
| **可落地的具体方案** | 每个 attempt 完成后写 `attempt_infra.json`（sidecar），包含：attempt_category、execution_verdict、failure_code、retryable。重启/恢复时优先读 sidecar 避免重新分类。skip-existing 逻辑：sidecar 存在且 `attempt_category != run_failed` 则 skip。 |

---

## 六、开发流程与质量保障层

### R19. 采纳 autolab 的多层门禁（而非单层 SHA256）

| 维度 | 内容 |
|------|------|
| **来源** | autolab 14 层 gate cascade |
| **当前计划** | 只有 SHA256 一层硬防线。 |
| **autolab 的做法** | 见上述 R7。关键差异在于：autolab 有**语法规约门禁**（`python -m py_compile`）、**小输入正确性门禁**（参考实现验证）、**禁止 API 正则门禁**（`grep` 不准用的 API）、**白名单门禁**（不允许新增文件）。 |
| **可落地的具体方案** | 在 `ValidityGateChain` 中第一版增加：① `SyntaxGate`（`python -m py_compile` 编译检查）；② `BannedImportGate`（正则匹配不准 import 的模块）；③ `OutputExistGate`（预期输出文件存在性检查）。 | 

### R20. 采纳 mini-swe-agent 的 FormatError + 可恢复错误模式

| 维度 | 内容 |
|------|------|
| **来源** | mini-swe-agent `exceptions.py`（FormatError） |
| **当前计划** | 无 Agent 输出格式错误的恢复机制。 |
| **mini-swe-agent 的做法** | `FormatError` 是受控异常，在 parse 失败时被 raise，异常消息是**包含纠正提示的模板化消息**，作为后续 LLM 调用的输入。格式：`{format_error_template.render(错误详情)}`。Agent 看到纠正消息后自己修正。 |
| **可落地的具体方案** | Agent 输出解析失败时，不直接报错，构造 `FormatError` 消息：① 说明解析失败的具体原因（缺少字段/类型错误/格式不符合 schema）；② 给出正确的 schema 示例；③ 将整个纠正消息作为下一条 user message 发给 Agent。最多 2 次纠正重试。 |

---

## 七、现有设计计划中应修正的技术细节

### R21. 修正 SEARCH/REPLACE 策略栈的 3 个事实错误

| 维度 | 内容 |
|------|------|
| **来源** | aider `search_replace.py` 实际代码 |
| **当前计划** | `04_ExecutorAgent.md` 第 3.3 节说「四层策略栈」，描述了 `dmp_apply`（字符级）作为第 4 层。 |
| **事实** | aider `editblock_strategies` 只有 3 层（`search_and_replace` → `git_cherry_pick` → `dmp_lines_apply`）。字符级 `dmp_apply` 不在 active strategies 中。`replace_closest_edit_distance` 是死代码（被 `return` 阻断）。 |
| **修正方案** | 改文档为「三层策略栈」，移除字符级 dmp 的说法。fuzzy fallback 需要额外阅读 aider 的 `replace_most_similar_chunk()` 和 `try_dotdotdots()` 来理解可用的模糊匹配能力。 |

### R22. 修正对 aider 许可证的判断

| 维度 | 内容 |
|------|------|
| **来源** | aider `LICENSE.txt`（Apache 2.0） |
| **当前计划** | 之前的审核假设 aider 是 AGPL。 |
| **事实** | aider 使用 **Apache 2.0** 许可证，无 AGPL 传染性。可以商业使用，直接复用代码。 |
| **修正方案** | 文档中许可证声明修正为 Apache 2.0。 |

---

## 反哺设计总结

| 编号 | 反哺设计 | 来源 | 反哺深度 | 实施优先级 |
|------|----------|------|----------|-----------|
| R1 | Middleware 栈架构 | DeepAgents | 架构级（重构计划） | P1 |
| R2 | System Prompt 四段组装 | DeepAgents | 架构级 | P1 |
| R3 | 三步预算检查 + 全局限制 | mini-swe-agent | 功能级 | P1 |
| R4 | JSON 恢复 + 截断恢复 | Arbor | 功能级 | P1 |
| R5 | 三层权限引擎 | OpenCode/MIMO | 架构级 | P1 |
| R6 | DoomLoop 检测 | MIMO | 功能级 | P1 |
| R7 | ValidityGateChain 级联 | autolab | 架构级 | P1 |
| R8 | Hidden Seed / 测试隔离 | autolab | 功能级 | P2 |
| R9 | Idea 多样性守卫 | AutoScientists | 功能级 | P2 |
| R10 | 红线自检 + 蜜月期 | AutoSOTA | 功能级 | P2 |
| R11 | 分布式停滞检测 | AutoScientists | 功能级 | P2 |
| R12 | 冠军竞争检测 | AutoScientists | 功能级 | P1 |
| R13 | 固定时间预算 + git 前沿 | autoresearch | 模式级（新增实验模式） | P2 |
| R14 | 日志解析式指标提取 | AI-Scientist | 功能级 | P2 |
| R15 | 锚定连续评分 | autolab | 算法级 | P2 |
| R16 | Gating Predicate + 排除计分 | SWE-Together | 功能级 | P1 |
| R17 | 统一事件总线 | Arbor | 架构级 | P1 |
| R18 | Sidecar + skip-existing | SWE-Together | 功能级 | P2 |
| R19 | 多层门禁（超越 SHA256） | autolab | 功能级 | P2 |
| R20 | FormatError 可恢复错误 | mini-swe-agent | 功能级 | P2 |
| R21 | SEARCH/REPLACE 事实修正 | aider 源码 | 文档修正 | P0 |
| R22 | 许可证修正 (Apache 2.0) | aider 源码 | 文档修正 | P0 |

**P0 = 立即修正文档错误**  
**P1 = 建议纳入第一版实现**  
**P2 = 建议纳入第二版或后续迭代**

---

## 实施路径建议

将上述反哺设计按照「对核心闭环的必要性」分为三条实施路径：

### 路径 A：第一版必须（最小闭环的骨架）
- R3（三步预算）+ R4（JSON 恢复）+ R5（权限引擎）+ R7（门禁级联）+ R12（冠军竞争）+ R16（Gating Predicate）+ R17（事件总线）+ R21/R22（文档修正）
- 这些组件缺失将使第一版无法上线。

### 路径 B：第一版建议（最小闭环的血肉）
- R1（Middleware 栈）+ R2（Prompt 组装）+ R6（DoomLoop）+ R14（日志解析）+ R18（Sidecar）+ R20（FormatError）
- 这些组件确保第一版的可维护性和调试效率。

### 路径 C：后续迭代
- R8（Hidden Seed）+ R9（多样性守卫）+ R10（红线自检/蜜月期）+ R11（分布式停滞）+ R13（固定预算模式）+ R15（锚定评分）+ R19（多层门禁）
- 这些组件提升系统质量和研究决策质量，可在第一版验证闭环后增量添加。
