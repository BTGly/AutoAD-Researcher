# AutoAD 实验 Agents 设计与开发计划文档索引

> 范围：仅覆盖“中间实验自迭代系统”。  
> 不覆盖：前端用户意图对齐 Agents、最终用户报告/展示 Agents。

## 参考覆盖说明

本轮材料中“22 个”指 22 个核心运行、科研自动化、编程、实验和异常检测项目；另有 6 个文档处理与提示词参考项目。因此本设计实际吸收的是：

- 22 个核心运行/实验项目；
- 6 个文档/提示词项目；
- 合计 28 个参考项。

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
   - 非阻塞训练进程、heartbeat、Sentinel；
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

### 9 天冲刺执行总览

> 开发顺序按 01-06 的 PR 串行，但执行节奏按以下功能切片安排。
> 前 5 个切片打通主轴，后 3 个切片全部用于测试。每个切片产出真实 artifact。

| 切片 | 覆盖计划 | 产出验证 artifact | 说明 |
|------|----------|-------------------|------|
| **P1** 入口打通 | PR-001A + 计划 01 | session.json, experiment_prepare Job | TaskBridge unlock → ExperimentStarter |
| **P2** 环境+基线 | 计划 01 (续) | EnvironmentSnapshot, baseline metrics, protected_hashes.json | 接线现有 Environment 子系统 |
| **P3** 执行闭环 | 计划 04 + 计划 03 | patch.diff, smoke pass, experiment Job, metrics.json, AttemptCategory | Worktree → Executor → Job → 失败分类 |
| **P4** 认知闭环 | 计划 02 | Idea Node, CycleDecision, ObservationSnapshot, CognitiveCommit | Coordinator → IdeaTree → DISPATCH |
| **P5** 验证闭环 | 计划 05 | OutcomeCard, KEEP/DISCARD, champion, B_test gate | NoiseFloor → DecisionEngine → Reflection |
| **P6** 集成测试 | 全链路 | 10 个 case pass/fail 矩阵 | 固定任务 + 故障注入（见 07 §7） |
| **P7** 真实运行 | 全链路 | 真实 GPU 端到端 artifact | 至少两轮真实异常检测实验 |
| **P8** 封板 | 全链路 | release tag, 演示 artifact, 安装说明 | 修 bug, 删 dead code, 冻结版本 |

**P6 开始禁止再引入新架构组件。**
