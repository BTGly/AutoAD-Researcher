# AutoAD 实验 Agents 设计包 — 开发计划审核报告

> 审核日期：2026-07-16  
> 审核范围：`notes/后端开发/AutoAD_实验Agents设计包/`（共 8 个 markdown 文件）  
> 审核维度：目标与范围、排期与里程碑、资源与协作、技术方案、风险与质量、逻辑一致性

---

## 🔴 高优先级问题

### H1 — 全文档无任何排期与里程碑日期

**问题定位：** 全部 8 个文件，涵盖 20+ PR（001A、01A–01D、02A–02E、03A–03E、04A–04E、05A–05E、06A–06F），未给出任一 PR 的预估工期、交付日期、关键路径。

**潜在风险：** 计划不可排期、不可追踪；无法判断第一版需 3 个月还是 2 年才能交付；管理层无法做资源承诺和进度管理。

**需补充：** 每 PR 预估人天/周数；标注关键路径与最早可行交付日期；区分里程碑与阶段交付物。

---

### H2 — 人力资源配置完全缺失

**问题定位：** 未提及团队人数、角色分工（后端/ML/QA/DevOps）、各 PR 负责人、投入工时、是否存在人力复用冲突。

**潜在风险：** 无人力基线则任何排期承诺均不可信；20+ PR 可能因人力不足导致长时间串行等待。

**需补充：** 人力角色矩阵（Role × PR 矩阵）、各阶段投入估算（FTE × 时长）。

---

### H3 —「DeepAgents」为核心依赖但未定义来源与就绪状态

**问题定位：**

- `create_deep_agent()` 在 `01_实验Agents大框架.md:381` 作为 Agent 工厂核心方法出现，后续文件全面依赖
- `DeepAgentsAgent`、`SummarizationMiddleware`、`CognitiveTaskRunner` 等在架构中承担关键角色
- 未说明 DeepAgents 是已有内部库、待开发组件还是第三方框架
- 未确认其是否兼容 Python 3.12+/asyncio、是否有文档或测试

**潜在风险：** **最大单一技术盲区。** 若 DeepAgents 不存在或不可用，Coordinator、Executor、ReflectionAgent、HealthDiagnosisAgent、StrategyDiagnosticAgent 全部悬空，整套系统无法落地。

**需补充：** 明确 DeepAgents 的来源、开发状态、License、兼容性；若为自研需出独立开发计划与排期；若为框架需确认版本并可复现构建。

---

### H4 — 外部代码复用存在 License 风险

**问题定位：**

| 引用来源 | 文件位置 | 复用内容 |
|---------|---------|---------|
| aider | `04_ExecutorAgent.md:69` | `flexible_search_and_replace()`、`RelativeIndenter` |
| SWE-Together | `05_ExperimentJob.md:152` | `eval_infra_sentinel.py` 设计 |
| MiMo (opencode) | `07_收敛.md:53` | `stableStringify()`、`stepSignature()` |
| software-agent-sdk | `07_收敛.md:78` | `StuckDetector` 类及 5 个方法 |
| AutoSOTA | `06_实验有效性.md:33` | 三层 SHA256 防御策略 |

**潜在风险：** aider 采用 GPL 许可，若直接内联复制或将项目整体链接发布，可能触发 GPL 传染性条款，要求整个项目开源。其他仓库的 License 亦未逐一核查。

**需补充：** 逐项确认各外部仓库的 License，确定复用方式（内联复制 / submodule / pip install 依赖 / 重写兼容实现），必要时联系法务。

---

### H5 — 无任何风险登记册

**问题定位：** 7 个 markdown 文件中没有一处列出项目级或技术级风险项、风险等级、影响范围与应对措施。

**潜在风险：** 当 DeepAgents 不可用、GPU 资源竞争、第三方仓库停更、V2 系统变更导致 001A 阻塞等风险发生时，项目无预案，可能退化为被动救火。

**需补充：** 建立《风险登记册》，至少覆盖：
- 技术风险：DeepAgents 不可用、GPU 碎片化/竞争、多进程文件锁竞争
- 进度风险：PR 依赖链阻塞、外部仓库版本演进破坏兼容性
- 资源风险：GPU 可抢占导致训练被中断、磁盘容量超限
- 每项标注：风险描述 → 概率 × 影响 → 应对措施 → 责任人

---

### H6 — PR-001A（前置条件）当前状态不明

**问题定位：** `02_ExperimentSession.md:52-73` 列出 PR-001A 需修改 `task_bridge.py`、`orchestrator.py`、`server/routes/runs.py`，并新增 `assistant/v2/experiment/starter.py`。但未说明：
- 这些文件当前是否存在、处于什么状态（开发中 / 已合入 / 不存在）
- 是否由同一团队维护、是否需要跨 PR 合入
- 001A 的完成标准是否已有依赖方确认

**潜在风险：** 若 V2 系统未就绪或 001A 合入需跨团队排队，整套实验系统无触发入口，后续所有 PR 阻塞。

**需补充：** 标注 001A 涉每文件的当前状态、维护方；确认 001A 的完成标准与合入时序；若能跳过 001A 直接开发 01–06 的纯服务端组件，需说明与 001A 的对接接口约定。

---

## 🟡 中优先级问题

### M1 — PR 间依赖关系未完整建模

**问题定位：** `00_README.md:56-72` 给出推荐开发顺序，但无依赖拓扑图。典型缺失依赖：
- PR 03（ExecutorAgent）依赖 01（Session/Env）和 04（Job/Runner）——但 04 本身也依赖 01
- PR 02（Coordinator）依赖 01/03/04/05
- 未标注哪些 PR 可并行（如 01B Probe 与 04A Job Schema 无依赖，可并行）

**潜在风险：** 任务调度时出现不合理串行等待，资源浪费。

**需补充：** 绘制 PR 依赖 DAG 图；标注每个 PR 的前置条件与后续被依赖关系；明确并行窗口。

---

### M2 —「第一版边界」存在模糊地带

**问题定位：**
- `01_实验Agents大框架.md:784-793`：「第一版不要求多机调度、K8s、自动 Docker 构建、复杂树搜索算法……」
- `05_ExperimentJob.md:107`：「第一版正式边界：single-node, single-GPU, single training process」
- `05_ExperimentJob.md:122`：「后续支持单机多 GPU 时应新增 LaunchProfile.TORCHRUN_LOCAL + CgroupV2ExecutionScope」
- 未界定第一版是否覆盖「单机多卡 DataParallel」「单机多卡 DDP」等非 torchrun 但多 GPU 的场景；也未说明检测到 `torchrun` 或 `WORLD_SIZE>1` 时的行为已确定并集成在哪个 PR 中。

**潜在风险：** 若目标实验任务广泛使用多 GPU，第一版立即不可用；范围蔓延风险——开发者可能试图「顺手支持单机多卡」，导致第一版边界漂移。

**需补充：** 在第一版范围声明中增加明确的 GPU boundary 表格（哪些场景 SUPPORTED、哪些会 UNSUPPORTED 并返回明确错误码）。

---

### M3 — NoiseFloor 预算设定存在内在冲突

**问题定位：** `06_实验有效性.md:189-190`：「Noise Floor 默认预算上限：min(5 次 baseline run, Session GPU 预算的 10%)」。当 Session GPU 预算仅够 <3 次 baseline 时标记 UNCALIBRATED，且「禁止宣称小幅提升」。

**潜在风险：** 大量实际实验的目标就是微调参数获得小幅提升，若因预算不足系统直接进入 UNCALIBRATED 且无后续行动策略，可能陷入「无法决策」的死锁——Coordinator 不 prune、不 stop、不 champion，只能空转。

**需补充：** UNCALIBRATED 状态下 Coordinator 的明确行动路径（继续/停止/降级确认/通知用户）；增加「预算不足 3 次 baseline 时是否应提前报错而非开始探索」的规则。

---

### M4 — CognitiveBudget 层级关系与传递机制未定义

**问题定位：**
- `01_实验Agents大框架.md:700-712` 定义 CognitiveBudget 为简单的硬限制（max_calls / max_cost / max_steps），每次 query 前检查
- `03_ResearchCoordinator.md:224-232` 新增 `CognitiveBudget` schema（max_calls / max_tokens / max_compact_cycles / max_exploratory_cycles / max_subagent_calls / max_wall_seconds）
- `04_ExecutorAgent.md:63` 要求 ExecutorAgent 有「最大 step/cost/wall」，但未说明其 budget 是共享 Coordinator 的还是独立的
- `07_收敛.md:310-314` PR-06B 再次提出 CognitiveBudget / Cost ledger

**潜在风险：** 三处出现认知 budget 概念但定义不一致（硬限制 vs 结构化配置），子 Agent（Executor、IdeaExplorer）的调用是单独记账还是从 Coordinator 预算中扣除未定义。可能导致子 Agent 耗尽全局预算而未告知 Coordinator。

**需补充：** 统一 CognitiveBudget 模型；定义父预算→子预算的传递/扣除/溢出处理逻辑；明确各 Agent 角色各自维护独立 ledger 还是共享 ledger。

---

### M5 — 深度依赖的第三方仓库无版本锁定

**问题定位：** 外部源码引用采用固定绝对路径（`/root/autodl-tmp/repos/<name>/…`），未锁定 commit SHA、未说明是否随项目分发：

| 路径 | 引用文件 |
|------|---------|
| `repos/aider/aider/coders/search_replace.py` | `04_ExecutorAgent.md:69` |
| `repos/SWE-Together/src/eval_infra_sentinel.py` | `05_ExperimentJob.md:152` |
| `repos/mimo-code/packages/opencode/…` | `07_收敛.md:53` |
| `repos/software-agent-sdk/…` | `07_收敛.md:78` |
| `repos/AutoSOTA/cli_guide.md` | `06_实验有效性.md:33` |

**潜在风险：** 环境迁移或 CI 执行时路径失效；第三方仓库更新引入行为变更；无法复现构建。

**需补充：** 锁定各依赖的 commit SHA；说明是否通过 vendoring 目录、git submodule 或 pip requirements 引入；在 CI 中验证检出一致性。

---

### M6 — AD-AgentBench 全链路测试基准无工作量估算

**问题定位：** `07_收敛.md:332-355` PR-06E 提出建立包含 10 个 Case 的 fixture 仓库，涉及模拟 GPU、确定性 metrics、故障注入脚本、replay harness。文档仅用一行「fixture repos」概括，无工作量估算、无依赖分析。

**潜在风险：** 验收资产可能因估算不足而延期；10 个 Case 中有些需要构造完整的「fake repo + fake GPU + fake metrics」管线，工作量可能等于一个中型 PR。

**需补充：** 评估 AD-AgentBench 各项 fixture 的开发工作量；确认是否需要独立 PR 或与各 PR 的验收条件合并；明确 replay harness 的维护策略。

---

### M7 — Artifact 存储后端与容量策略未定义

**问题定位：** `01_实验Agents大框架.md:720-758` 给出详尽目录结构（`runs/<run_id>/experiment/<session_id>/…`），但未说明：
- 存储介质（本地磁盘 / NAS / S3 / 云对象存储）
- 是否支持分布式访问
- 磁盘容量上限与清理/归档策略
- checkpoint 文件的保留周期

**潜在风险：** 长周期实验可能产生大量 artifact（`checkpoint + logs + metrics + patch + heartbeat`），单 session 可能达到数十 GB。无容量规划可能导致任务中途磁盘写满。

**需补充：** 存储后端选型、容量估算公式、清理策略（TTL / LRU / 用户手动归档），以及首次实现是否只用本地磁盘。

---

### M8 — SEMANTIC_DEVIATION 判定标准与交互边界不清晰

**问题定位：** `04_ExecutorAgent.md:204-219` 定义 Executor 返回 SEMANTIC_DEVIATION 的场景（修改 mechanism / loss / objective / evaluation protocol / dataset split），然后 Coordinator 决定是否创建 child Idea。但：
- 未定义谁来判定「改 loss function 是否算语义偏移」——许多实验的核心假设就是修改 loss
- 若 Coordinator 每次遇到 SEMANTIC_DEVIATION 都要决策，可能频繁中断实验流程
- 无兜底：若 Coordinator 持续为同一 idea 创建 child（child 仍 SEMANTIC_DEVIATION），形成无限循环

**潜在风险：** 实验流程被 SEMANTIC_DEVIATION 反复打断；或在边缘情况下出现 child→SEMANTIC_DEVIATION→child 的死循环。

**需补充：** 定义 SEMANTIC_DEVIATION 的刚性边界（硬性不可越）与软性边界（提示 Coordinator 但可确认继续）；增加循环检测（同一 parent 衍生出超过 N 个 SEMANTIC_DEVIATION 的 child 时应触发 StuckDetector）。

---

## 🟢 低优先级问题

### L1 — StuckDetector 第 5 模式为 stub

**问题定位：** `07_收敛.md:86-87` 明确标注 Mode 5（Context Window Error）的方法定义为 `return False`（stub）。

**潜在风险：** 作为第一版的一部分被列出但永不触发，属于 dead code；若实际出现 context window error 循环则不会被检测。

**需补充：** 确认是否需在 first version 中实现；若不实现在文档中标注「first version 暂不实现」或移除该模式。

---

### L2 — ObservationSnapshot 冲突场景未覆盖

**问题定位：** `03_ResearchCoordinator.md:211-215` 崩溃恢复规则：
- Snapshot 存在且 `tree_revision` 未变 → 重做 IDEATE
- 但 `outcome_refs`（snapshot 字段之一）可能已变化（新增 attempt）而 `tree_revision` 未变

**潜在风险：** 跳过 OBSERVE，导致基于过期观察进行 IDEATE，可能产生已失败过的重复 idea。

**需补充：** 在 Snapshot 中增加 `outcome_refs_hash` 或 `evidence_hash` 作为辅助判断；或改为「Snapshot 存在 + tree_revision 未变 + outcome_refs 未变 → 重做 IDEATE」。

---

### L3 — Probe 超时值未定义

**问题定位：** `02_ExperimentSession.md:174-177` 列出 12 项探测，要求所有命令有 timeout，但未给具体超时建议。

**潜在风险：** 网络依赖的 probe（如 pip index 可访问性探测、git clone 探测）若未设定超时可能阻塞整个环境准备 Job 数十分钟。

**需补充：** 提供默认超时值建议（如命令类 30s、网络类 60s、GPU probe 10s）；在 Probe 接口中要求 timeout 为必传参数。

---

### L4 —「finally-save」机制在极端条件下不保证执行

**问题定位：** `01_实验Agents大框架.md:414` 引用 mini-swe-agent 的「finally-save」模式要求 trajectory/attempt/checkpoint 强制落盘。但 Python 进程中，被 SIGKILL、OOM Kill 或 `torch.distributed.elastic` 管理进程终止时，`finally` 块不保证执行。

**潜在风险：** 极端情况下 attempt 状态丢失，无法恢复。

**需补充：** 明确最终实现中除 `finally` 块外，是否有 `atexit`、SIGTERM handler、watchdog 等兜底策略；在文档中标注「尽力而为」边界。

---

### L5 — Environment Adapter 未覆盖 PDM/Poetry

**问题定位：** `02_ExperimentSession.md:29` 列出 UvVenv / PipVenv / Conda / ExistingPython 四种 Adapter，未涉及 PDM、Poetry 等现代 Python 项目管理工具。

**潜在风险：** 遇到仅支持 Poetry 或 PDM 的项目时，环境准备流程无法自动识别和适配。

**需补充：** 确认是否需纳入或已有 fallback 方案（如降级到 pip install -r requirements.txt）；说明各 Adapter 的优先级与冲突处理。

---

### L6 — 无 Python 版本下限与 CUDA 兼容性声明

**问题定位：** 全文未指定项目最低 Python 版本要求，也未提及 CUDA Toolkit 版本兼容性（CUDA 11.8 vs 12.4 的 torch 兼容差异）。

**潜在风险：** GPU probe 检测 CUDA runtime 正常但 torch 版本不兼容时，定位根因困难；不同实验仓库可能要求不同 Python 版本。

**需补充：** 补充环境兼容矩阵（Python 3.10+/3.11+/3.12+ 支持声明、CUDA 11.x/12.x 兼容说明）；若 Agent 自身运行时需特定版本，也应注明。

---

## 📋 整体审核结论

**核心风险等级：偏高。**

该设计包在**系统架构层面表现出高水平**——各 Agent 角色职责清晰、状态真源分层合理、确定性治理路线审慎。但其作为一份**开发计划**在以下四个基础维度上存在严重缺失：

| 维度 | 缺失程度 | 主要问题 |
|------|---------|---------|
| **排期与里程碑** | ❌ 完全缺失 | 20+ PR 无任何日期、工期、关键路径 |
| **人力资源** | ❌ 完全缺失 | 无人头、无角色、无投入比 |
| **风险管控** | ❌ 完全缺失 | 无风险登记册、无应对预案 |
| **关键技术依赖确认** | ⚠️ 严重不足 | DeepAgents 未定义来源，5 个外部仓库未锁定版本与 License |

**最关键的三项待办（按影响排序）：**

1. **确认 DeepAgents 框架就绪状态** —— 否则整套基线不可行
2. **补充排期与人力基线** —— 否则计划不可管理
3. **审计外部代码 License** —— 避免后期法务风险

在技术细节层面，认知预算的层级传递、SEMANTIC_DEVIATION 判定边界、NoiseFloor UNCALIBRATED 状态的行动路径也需要尽快明确，避免进入开发后反复打回重做。
