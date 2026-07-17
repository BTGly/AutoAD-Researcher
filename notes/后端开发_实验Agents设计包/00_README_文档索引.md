# AutoAD 实验 Agents 设计与开发计划文档索引

> 范围：仅覆盖“中间实验自迭代系统”。  
> 不覆盖：前端用户意图对齐 Agents、最终用户报告/展示 Agents。

## 参考覆盖说明

本轮材料中"22 个"指 22 个核心运行、科研自动化、编程、实验和异常检测项目；另有 6 个文档处理与提示词参考项目。因此本设计实际吸收的是：

- 22 个核心运行/实验项目；
- 6 个文档/提示词项目；
- 合计 28 个参考项。

## 外部参考复用等级

所有外部来源的代码、模式或设计必须按以下五级标签分类，并在各计划文档中标注。禁止笼统使用"直接复用"表述。

| 标签 | 含义 | AutoAD 行为 |
|------|------|------------|
| `[COPY]` | 代码可直接纳入 | 保留版权、许可证、来源和必要的最小 import 修改 |
| `[PORT]` | 小型函数跨语言移植 | 保留算法语义，自行写目标语言版本和测试 |
| `[ADAPT]` | 算法主体可用，但依赖外部数据模型 | 提取算法，重写 AutoAD 事件/schema 适配层 |
| `[ADAPT-LATER]` | 同上，但本轮不实现 | 标记延期，主链闭环后再接 |
| `[REIMPL]` | 只有行为文档或不可复用实现 | 根据公开行为描述独立实现 |
| `[REFER]` | 仅借鉴架构或设计模式 | 不复制源码，不形成第三方运行时依赖 |

所有外部引用必须记录：`source_repository`、`source_commit`、`source_path`、`license`、`reuse_level`、`autoad_target`、`estimated_effort`、`attribution_required`。禁止使用 `/root/autodl-tmp/...` 作为实现时路径。

### 本轮复用矩阵

**"参考优先"设计原则：不确定的开发操作，先查看参考仓库中成熟项目的做法，再决定如何开发。** 每个组件必须标注 Reference / Reuse level / Upstream source / Adapted behavior / AutoAD divergence / Why divergence is necessary。

复用等级按 `[COPY] / [PORT] / [ADAPT] / [REIMPL] / [REFER] / [COMPOSED]` 六级分级：

| 等级 | 含义 | 要求 |
|------|------|------|
| `[COPY]` | 直接复制 | 许可证兼容 + 接口适合 + vendor 并保留 LICENSE/NOTICE |
| `[PORT]` | 语言移植 | 小函数跨语言改写，保留算法语义 |
| `[ADAPT]` | 适配改造 | 保留核心算法，适配 AutoAD 数据模型 |
| `[REIMPL]` | 独立实现 | 按公开行为重新实现，不复制源码 |
| `[REFER]` | 架构参考 | 只参考设计模式，自研实现 |
| `[COMPOSED]` | 组合模式 | 由多个成熟模式组合，无单一可复制上游。需满足：组件尽量小、有故障注入测试、有恢复/回退、不承担语义推断、不新增第二套状态真源 |

| 来源 | 等级 | 本轮动作 |
|------|------|----------|
| AutoSOTA SHA guard | `[REIMPL]` | 根据 `cli_guide.md` 和已观察到的 Python 移植行为独立实现 ProtectedArtifactGuard |
| SWE-Together failure classifier | `[COPY]` / `[ADAPT]` | vendor 原文件 + AutoAD wrapper，仅作为实验后分类器，不作为运行时 watchdog |
| OpenHands StuckDetector | `[ADAPT-LATER]` | 事件类型耦合明显，主链闭环后再接完整 5-mode |
| MiMo `stableStringify` | `[PORT-PENDING-LICENSE]` | TypeScript → Python 改写，确认许可证后再落地 |
| Arbor | `[REFER]` | Idea Tree、Coordinator/Executor 切分、B_dev/B_test |
| AI-Scientist | `[REFER]` | 模板实验和 subprocess 循环 |
| Claude Code internals | `[REIMPL]` | 工具注册表模式（Tool/inputSchema/call）、权限模型（allow/ask/deny）、会话恢复（hydrate/findUnresolved）、文件状态缓存 |
| aider SEARCH/REPLACE | `[REIMPL]` | 根据三个策略自行实现，不复制 AGPL 主体 |
| DVC experiment refs/apply | `[REFER]` | Candidate branch lifecycle、promotion apply、实验复现 |
| Optuna FrozenTrial + JournalStorage | `[REFER]` | immutable candidate、champion selection、追加式 promotion journal |
| `[COMPOSED]` PromotionJournal | — | = DVC experiment apply + Git merge refs + Optuna JournalStorage，组合多个成熟模式，无单一可复制上游实现 |

## 文档列表

1. `实验Agents_大框架.md`
   - 定义系统边界、总体架构、角色、状态真源、循环、成本控制和开发分层。

2. `开发计划01_ExperimentSession与环境准备.md`
   - 接通实验 Session；
   - 复用并接线现有 Environment 子系统；
   - 补充真实环境探测和环境快照。

3. `开发计划02_ResearchCoordinator与IdeaTree.md`
   - 实现持久 Research Coordinator；
   - 实现连续 ideation、Idea Tree、CognitiveCommit；
   - 实现 Compact / Exploratory 两级认知循环。

4. `开发计划03_ExecutorAgent与代码修改闭环.md`
   - 实现临时 ExecutorAgent；
   - git worktree 隔离；
   - SEARCH/REPLACE；
   - 有界代码修复和 InterventionContract。

5. `开发计划04_ExperimentJob_GPU资源与训练监控.md`
   - 实现实验 Job；
   - GPU ResourceLease；
   - 非阻塞训练进程、heartbeat、RuntimeWatchdog + PostRunFailureClassifier；
   - LLM 仅作事件触发的保底诊断。

6. `开发计划05_实验有效性_Reflection与决策.md`
   - 四层有效性；
   - noise floor；
   - Reflection；
   - champion、KEEP-WHY、补 seed、派生假设和 B_test gate。

7. `开发计划06_收敛_认知预算与端到端验收.md`
   - ConvergenceMonitor；
   - StuckDetector；
   - CognitiveBudget；
   - StrategyDiagnosticAgent；
   - 全系统回放、故障注入和科研闭环验收。

## 推荐开发顺序

```text
PR-001A（V2→实验接线 = execution_mode 解锁 + ExperimentStarter）
   ↓
计划 01（ExperimentSession + Environment 接线）
   ↓
计划 04 的 Job 基础与 Runner 改造
   ↓
计划 03（ExecutorAgent + worktree + SEARCH/REPLACE）
   ↓
计划 05 的确定性有效性部分（EvaluationContract + SHA guard + Validity + NoiseFloor）
   ↓
计划 02（Coordinator + IdeaTree + CognitiveCommit）
   ↓
计划 05 的 Reflection 与持续决策部分
   ↓
计划 06（收敛 + 预算 + 端到端验收）
```

原因：

- 不先解锁 execution_mode，整个实验系统没有触发入口；
- 没有 Session、Environment、Job 和 Runner，Agent 只能生成文本；
- 没有有效性契约，Coordinator 会基于不可信指标自我迭代；
- Idea Tree 和持久 Coordinator 应在第一个真实单轮闭环已经可靠后接入；
- 收敛、策略调整和复杂记忆属于多轮能力，最后实现。
