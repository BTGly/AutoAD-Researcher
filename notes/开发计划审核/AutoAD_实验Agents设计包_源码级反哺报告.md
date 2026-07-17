# AutoAD 实验 Agents — 参考项目源码级深度学习反哺报告

> 基于对 5 个核心参考仓库的完整源代码深度阅读，输出可直接反哺到 AutoAD 设计中的具体模式、协议、数据结构和代码架构。

---

## 目录

1. [Agent 框架层：复用 deepagents 的内置能力](#1-agent-框架层复用-deepagents-的内置能力)
2. [认知循环层：吸收 Arbor 的约束块与收敛检测](#2-认知循环层吸收-arbor-的约束块与收敛检测)
3. [Agent 协作层：引入 AiScientist 的 File-as-Bus 与 IOU 平衡](#3-agent-协作层引入-aiscientist-的-file-as-bus-与-iou-平衡)
4. [实验契约层：采纳 AI-Scientist 的模板系统与评审门禁](#4-实验契约层采纳-ai-scientist-的模板系统与评审门禁)
5. [调度与学习层：借鉴 Claw-AI-Lab 的队列调度与进化系统](#5-调度与学习层借鉴-claw-ai-lab-的队列调度与进化系统)
6. [总结：AutoAD 各组件的优化映射](#6-总结autoad-各组件的优化映射)

---

## 1. Agent 框架层：复用 deepagents 的内置能力

### 1.1 Middleware 栈顺序 — 直接锁定复用，删除 CognitiveTaskRunner

**源码位置：** `deepagents/libs/deepagents/deepagents/graph.py:751-814`

deepagents 的 middleware 栈顺序是硬编码的，比 AutoAD 计划的 `CognitiveTaskRunner` 抽象层更成熟。当前 AutoAD 计划定义了一个 `CognitiveTaskRunner` 协议层 (`01_实验Agents大框架.md:393-434`)，但 deepagents 已经提供了等价的 SubAgentMiddleware。

**直接复用方案：** 删除 `CognitiveTaskRunner` 协议。AutoAD 的 6 种 Agent 角色直接映射为 deepagents 的 `SubAgent` 声明式配置：

```python
from deepagents import SubAgent, create_deep_agent

coordinator = create_deep_agent(
    model="gpt-5",
    system_prompt="You are a research coordinator...",
    subagents=[
        SubAgent(
            name="idea_explorer",
            description="Deep dive ideation for researchers",
            system_prompt="...",
            response_format=IdeaExploreOutput,  # Pydantic model
            permissions=[FilesystemPermission("/workspace/ideas", "read")],
        ),
        SubAgent(
            name="executor",
            description="Implement code changes",
            system_prompt="...",
            model="claude-sonnet",  # different model per subagent
            middleware=[CustomRepairMiddleware()],
        ),
        SubAgent(
            name="reflection",
            description="Analyze experiment results",
            system_prompt="...",
            response_format=ReflectionOutput,
        ),
    ],
    middleware=[CustomStrategyMiddleware()],
    checkpointer=SqliteSaver(path="./checkpoints"),
)
```

**直接节省工作量：** CognitiveTaskRunner 的设计、实现、测试（约 8 人天）可完全省去。deepagents 的 SubAgentMiddleware（868 行）直接提供子 Agent 上下文隔离、结构化输出提取、并行调用能力。

### 1.2 Summarization 触发策略 — 按角色差异化配置

**源码位置：** `deepagents/libs/deepagents/deepagents/middleware/summarization.py:284-296`

deepagents 的 SummarizationMiddleware 支持三种触发维度：
```python
# 按 token 数
("tokens", 170000)
# 按消息数
("messages", 50)
# 按百分比
("fraction", 0.85)
# 组合 AND
{"tokens": 100000, "messages": 40, "fraction": 0.75}
# 多条件 OR
[("messages", 40), ("fraction", 0.75)]
```

**AutoAD 的反哺：** Coordinator、IdeaExplorer、Executor 需要不同的 summarization 策略：

| Agent 角色 | 触发条件 | keep 策略 | 原因 |
|---|---|---|---|
| Coordinator | `("fraction", 0.8)` | `("fraction", 0.15)` | 保留足够上下文做决策 |
| IdeaExplorer | `("tokens", 200000)` | `("messages", 6)` | 允许深度推理 |
| Executor | `("tokens", 100000)` | `("messages", 10)` | 需要保留代码修改历史 |
| Reflection | `("messages", 30)` | `("messages", 5)` | 结果分析不需要长历史 |

每个子 Agent 有自己的 SummarizationMiddleware 实例（deepagents 默认行为），无需额外开发。

### 1.3 Tools 调用前的大参数截断 — 防止因大文件内容触发不必要的 Summarization

**源码位置：** `deepagents/libs/deepagents/deepagents/middleware/summarization.py:728-832`

deepagents 在 summarization 触发前，先尝试截断旧消息中的大工具调用参数（只截断 `write_file` 和 `edit_file`）。这个「先截参数、再决定是否 summarization」的顺序很关键。

**AutoAD 的适配：** ExecutorAgent 的 SEARCH/REPLACE 会产生大块代码文本，应在 SummarizationMiddleware 的 `truncate_args_settings` 中增加 `search_replace` 工具调用的截断配置。

### 1.4 Skills 渐进式加载 — 替代 AutoAD 的策略 Overlay 系统

**源码位置：** `deepagents/libs/deepagents/deepagents/middleware/skills.py:748-832`

depthagents 的 SkillsMiddleware 的设计优于 AutoAD 计划中的 StrategyPolicy overlay 方案：

| 特性 | deepagents Skills | AutoAD StrategyPolicy |
|---|---|---|
| 加载时机 | 元数据在 prompt，完整内容按需 `read_file` | 所有 overlay 全量注入 |
| 格式 | SKILL.md（YAML frontmatter + Markdown） | 未定义格式 |
| 源管理 | 多源路径，后覆盖前 | 无源管理 |
| 权限 | 可选 allowed-tools 字段 | 无 |
| 渐进式 | 列表在系统 prompt 中，内容不主动加载 | 无 |

**直接复用：** AutoAD 的 `DIVERSIFY_AXES`、`PREFER_MECHANISTIC_IDEAS` 等策略 overlay（`07_收敛.md:144-152`）应改为 deepagents SKILL.md 格式：

```yaml
---
name: diversify-axes
description: When research is over-concentrated, force exploration of new research axes
allowed-tools: tree_view tree_add_node tree_search
ttl: 3  # 可选自定义字段
---
# Diversify Axes Strategy

## When to apply
- Research axis concentration > 80% on one direction
- No new axes explored in last 5 cycles

## Instructions
1. Review the frontier view for unexplored research axes
2. ...
```

**节省工作量：** 约 12 人天（StrategyPolicy 的 overlay 管理、版本控制、审计日志、回滚机制，deepagents 已部分覆盖）。

### 1.5 HITL — 利用内置的 HumanInTheLoopMiddleware

**源码位置：** `deepagents/libs/deepagents/deepagents/graph.py:809-814`

deepagents 支持：
```python
interrupt_on = {
    "tree_add_node": InterruptOnConfig(
        schema=ProposedIdea,  # Pydantic model for human review
        when=lambda ctx: ctx.state.get("cycle_type") == "exploratory",
    ),
    "executor_dispatch": True,
}
```

**AutoAD 的适配：** 在 Coordinator 的关键决策点（create child idea、champion promotion、stop proposal）设置 `interrupt_on`，deepagents 自动暂停等待人工输入。HITL 状态自动序列化到 checkpoint（Arbor 已验证此模式可行）。

---

## 2. 认知循环层：吸收 Arbor 的约束块与收敛检测

### 2.1 IDEATE 约束块（Constraints Block）— 防止 LLM 遗忘

**源码位置：** `Arbor/src/coordinator/idea_tree.py:396`，方法 `get_constraints_block()`

返回的 Markdown 结构可直接用于 AutoAD 的 Compact Cycle 输入：

```
## TREE SHAPE
max_depth: 2 | depth-1: 5 nodes (3 done, 1 pending, 1 pruned)
depth-2: 12 nodes (8 done, 2 merged, 2 pruned)

## ROOT INSIGHT (current best global understanding — your priors)
[... root node's accumulated insight ...]

## PRUNED LESSONS (3 — these directions FAILED.
Do NOT re-propose any idea that shares the same hidden assumption
or mechanism class without explicitly explaining how it counters the lesson.)
- [1.2] hypothesis: "Increase model depth to improve detection"
  → insight: "Deeper models overfit on small anomaly datasets; no improvement on B_test"

## VALIDATED FINDINGS (5 — these are now part of the trunk's
working assumptions. Build on them; don't re-derive them.)
- [merged 1.1 score 87.3%] "Augmentation with CutPaste improves detection by 5%"
  → insight: "Synthetic anomaly generation is effective for this dataset family"
```

**AutoAD 当前缺失：** `03_ResearchCoordinator.md:111-120` 的 Compact Cycle 输入列表包含了 SessionSummary、FrontierView、OutcomeCards 等，但缺少 **PRUNED LESSONS** 和 **VALIDATED FINDINGS** 两个关键块。这直接导致 Coordinator 容易重复已失败的假设方向。

**建议实施：** 在 IdeaTree store 中增加 `get_constraints_block()` 方法，在每次 Compact Cycle 的 ContextPack 中注入该块。仅需在工作项 PR-02A（IdeaTree 与 CognitiveCommit）中增加约 60 行代码。

### 2.2 实验分类 — 引入 `needs_retry` 状态

**源码位置：** `Arbor/src/coordinator/tools/executor_run.py:62`，函数 `_classify_executor_outcome()`

Arbor 将实验结果分为 3 类而非 AutoAD 的 2 类：

| 条件 | Arbor 分类 | AutoAD 当前分类 |
|---|---|---|
| 产生有效分数 | `done` | SCIENTIFICALLY_EVALUABLE |
| 超时 / 错误 / max_turns | `needs_retry` | RUN_FAILED |
| 故意跳过 eval | `done` (skipped) | （无等价状态） |

AutoAD 的 AttemptCategory (`06_实验有效性.md:133-151`) 只有 `SCIENTIFICALLY_EVALUABLE / RUN_FAILED / PROTOCOL_VIOLATED`。这意味着一次 GPU OOM 就被标记为 `RUN_FAILED`，即使只是临时资源问题，也被排除出收敛计算。

**建议修改：** 增加 `RETRYABLE_FAILURE` 状态，OOM、CUDA error、disk full、network timeout 等基础设施问题归入此类，自动重试（最多 N 次），不影响收敛统计。PR-04 的 `run_failed` 检测表（`05_ExperimentJob.md:164-174`）自然可作为分类依据。

### 2.3 收敛检测的滑动窗口

**源码位置：** `Arbor/src/coordinator/convergence.py:72`

```python
@dataclass
class ConvergenceConfig:
    min_experiments: int = 4
    window_size: int = 5
    improvement_threshold: float = 0.001
    parent_exhaustion_count: int = 3
    warn_after: int = 3
    force_after: int = 5
    stop_after: int = 8
```

关键逻辑：
1. **滑动窗口速度**：取最近 `window_size` 个节点的最佳 delta / 窗口大小
2. **父节点耗尽检测**：同一父节点的连续 N 个子节点未改进 → 标记父节点为 exhausted
3. **三级警报**：`warn` (3) → `paradigm_shift` (5) → `stop` (8)
4. **信息注入**：警报直接格式化为 Markdown 注入到 Coordinator 的下一次调用上下文

**AutoAD 当前差距：** `ConvergenceMonitor`（`07_收敛.md:10-38`）列出了信号指标但未定义具体算法。Arbor 的 `compute_velocity()` + `find_exhausted_parents()` 可以直接复用。

### 2.4 上下文剪枝的锚点策略

**源码位置：** `Arbor/src/coordinator/context_prune.py:59`

Arbor 的剪枝策略不是简单的「截断最早 N 条消息」，而是：
1. 找到最近一次调用 `TreeView(format="constraints")` 的轮次作为锚点
2. 删除锚点之后所有助理消息的 `thinking`/`redacted_thinking`
3. 将 `text` 折叠为 `"[IDEATE reasoning elided post-commit]"`
4. 将 `LoadSkill` 的 tool result 替换为 `"[skill body elided post-IDEATE]"`

**AutoAD 当前差距：** Context Pruning（`03_ResearchCoordinator.md:166-186`）只定义了「做什么」（delete / truncate / preserve），没有定义「怎么做」。Arbor 的锚点策略可以直接适配到 deepagents 的 SummarizationMiddleware 之外，作为一个额外的 pruning pass。

### 2.5 Checkpoint-aware HITL

**源码位置：** `Arbor/src/coordinator/hitl.py:22` 和 `Arbor/src/coordinator/checkpoint.py:93`

Arbor 的 HITL 机制被序列化到 checkpoint：
```python
# checkpoint.py:93
pending_user: dict[str, Any] | None = None  # 镜像 AWAIT_USER 负载
```

恢复时从 `checkpoint.pending_user` 还原，重新渲染 `resume_prompt`。如果 HITL 超时（`asyncio.wait_for`），返回 `None`，系统继续自动运行。

**AutoAD 当前差距：** 全计划未定义 HITL。Arbor 的模式可以直接通过 deepagents 的 `HumanInTheLoopMiddleware` + `interrupt_on` 实现。

---

## 3. Agent 协作层：引入 AiScientist 的 File-as-Bus 与 IOU 平衡

### 3.1 File-as-Bus 的精确文件契约

**源码位置：** `AiScientist/src/aisci_domain_mle/constants.py:33-151`

AiScientist 定义了两类文件路径的精确读/写契约（以 GPU 实验场景为例）：

| 文件路径 | 写者 | 读者 | 读取方式 |
|---|---|---|---|
| `/workspace/impl_log.md` | ExecutorAgent | Coordinator, ReflectionAgent | grep 取最后一段 `=== Implement Session N ===` |
| `/workspace/exp_log.md` | ExperimentJob | Coordinator, ExecutorAgent | grep 取最后一段 `=== Experiment Session N ===` |
| `/workspace/hypothesis_registry.jsonl` | Coordinator | 所有 Agent | tail 读取 |
| `/workspace/experiments/` | ExperimentJob | Coordinator | 目录遍历 |
| `/workspace/champion.json` | Coordinator | 无（确定性代码消费） | 直接读取 |

**AutoAD 的适配方案：** 当前计划的 artifact 目录结构（`01_实验Agents大框架.md:720-758`）定义了文件路径但没定义「哪个 Agent 读哪个文件」的读/写契约。建议在每个 Agent 的 `system_prompt` 中注入一个「可读文件」表，让 Agent 自主决定何时读取哪些文件。

```
## Available Workspace Files
| Path | Content | Last Updated | Read by |
|---|---|---|---|
| environment/snapshot.json | GPU/CUDA/Python 环境配置 | ENV_READY | Coordinator, Executor |
| ideas/tree.md | 当前 Idea Tree 完整视图 | 每次 mutation | Coordinator |
| attempts/latest/outcome_card.json | 最新实验结果 | 每次 result integration | Coordinator, Reflection |
| champion.json | 当前最佳候选 | 每次 promotion | Coordinator |
```

### 3.2 Grep-based Last-Session 注入

**源码位置：** `AiScientist/src/aisci_domain_mle/orchestrator.py:406-411`

这是 File-as-Bus 的关键实践：每次子 Agent 启动时，不是加载完整的文件历史，而是只取**最后一段**：

```python
# AiScientist 模式：grep 取最后一段 exp_log
LAST_SEP=$(grep -n '^=== Experiment Session' exp_log.md | tail -1 | cut -d: -f1)
sed -n "${LAST_SEP},\$p" exp_log.md  # 只输出最后一段到 Agent
```

**AutoAD 的适配：** 当 Coordinator 调度 ExecutorAgent 时，不应该把整个 OutcomeCard 历史塞入上下文。应该只注入：
- 当前 idea 的 InterventionContract
- 最新的 OutcomeCard（最近的实验）
- IdeaTree 的 constraints block（Arbor 模式）

### 3.3 IOU 平衡监测

**源码位置：** `AiScientist/src/aisci_domain_mle/orchestrator.py:792-889`

```python
# 精确的阈值逻辑
if exp_count >= impl_count + 4:
    # "Running experiments without code changes is WASTED TIME"
elif exp_count >= impl_count + 2:
    # "experiment calls are outpacing implement calls"
elif impl_count >= exp_count + 3:
    # "VALIDATION GAP: writing code without validating it"
```

**AutoAD 的适配：** 在 ConvergenceMonitor 中增加两个计数器 `implement_attempts` 和 `evaluation_attempts`，当 imbalance 超过阈值时，生成 InterventionCard 注入到 Coordinator 的下一次决策上下文。实现成本约 40 行代码。

### 3.4 候选注册表（Candidates Registry）

**源码位置：** `AiScientist/src/aisci_domain_mle/candidate_registry.py:36-79`

```python
@dataclass(frozen=True)
class CandidateSnapshot:
    source: Path
    snapshot: Path  # candidates/candidate_001.csv
    registry_path: Path  # submission_registry.jsonl

def snapshot_submission(self, submission_path, *, reason, method_summary, metrics=None, eval_protocol="unknown"):
    next_index = len(sorted(self.candidates_dir.glob("candidate_*.csv"))) + 1
    shutil.copy2(submission_path, self.candidates_dir / f"candidate_{next_index:03d}.csv")
    self.append("candidate_detail", ...)

def select_champion(self, champion_path, *, rationale, metrics=None, eval_protocol="unknown"):
    self.append("champion_selected", champion_path=champion_path, rationale=rationale, ...)
```

**AutoAD 的适配：** 当前 ChampionStore（`06_实验有效性.md:281-292`）只保存当前 champion，覆盖式更新。改为 AiScientist 模式：每次 `PROMOTE_CANDIDATE` 先 `snapshot_submission()`，再 `select_champion()`。保留所有历史的不可变快照。

---

## 4. 实验契约层：采纳 AI-Scientist 的模板系统与评审门禁

### 4.1 实验模板契约

**源码位置：** `AI-Scientist/templates/nanoGPT_lite/prompt.json` 和 `seed_ideas.json`

AI-Scientist 的实验模板契约极其轻量（仅 2+5+1 个文件即可定义一个可自动化研究的领域）：

| 文件 | 内容 | 对应 AutoAD 设计的影响 |
|---|---|---|
| `prompt.json` | `system` + `task_description` | 替代 AutoAD 的厚 EvaluationContract，用 prompt 承载领域知识 |
| `seed_ideas.json` | `Name/Title/Experiment/Interestingness/Feasibility/Novelty` | 为 Coordinator 的 IDEATE 提供 few-shot |
| `experiment.py` | 接受 `--out_dir`，输出 `final_info.json` | 定义标准化实验输出协议 |
| `final_info.json` | `{条件: {means: {指标: 值}, stderrs: {...}}}` | 统一的实验结果格式，解耦执行与分析 |

**AutoAD 当前差距：** EvaluationContract（`06_实验有效性.md:12-27`）有 11 个字段，全部耦合在 Session 层，每个新实验仓库都需要复制全套适配器。AI-Scientist 的方案是：**prompt.json 承载领域语义**，`final_info.json` 承载结果数据，两者解耦。

**建议：** 在 PR-01（ExperimentSession）中增加可选的 `template_contract` 引用路径，如果存在则加载 `prompt.json` + `seed_ideas.json` 作为 evaluation 的一部分。

### 4.2 结果标准化协议

**源码位置：** `AI-Scientist` 中 `final_info.json` 的 schema

```json
{
    "<dataset_or_condition>": {
        "means": {"accuracy": 0.95, "f1": 0.93},
        "stderrs": {"accuracy": 0.02, "f1": 0.03},
        "final_info_dict": {"accuracy": [0.93, 0.95, 0.97]}
    }
}
```

**AutoAD 的适配：** 当前 OutcomeCard（`06_实验有效性.md:216-234`）定义了 11 个字段但都是元数据，没有标准化结果数据。建议在 Attempt 目录下增加 `metrics.json` 标准格式：

```json
{
    "conditions": {
        "default": {
            "metrics": {"accuracy": {"mean": 0.95, "std": 0.02, "values": [0.93, 0.95, 0.97]}},
            "resource": {"wall_sec": 3600, "peak_vram_mb": 24000}
        }
    }
}
```

这样 NoiseFloor、DecisionEngine、ChampionStore 都可以基于这个标准化格式消费，无需每个模块独立解析。

### 4.3 Ensemble Review 门禁

**源码位置：** `AI-Scientist/perform_review.py:126-243`

```python
# 5 个并行 review
reviews = [llm_call(reviewer_prompt, temperature=0.75) for _ in range(5)]

# Meta-reviewer 聚合
meta_review = llm_call(areachair_prompt, reviews_text)

# 分数取平均
for score in [Originality, Quality, Clarity, Significance, Soundness, 
              Presentation, Contribution, Overall, Confidence]:
    review[score] = int(round(np.mean([r[score] for r in parsed_reviews])))
```

| 维度 | 范围 | 标签 |
|---|---|---|
| Originality | 1-4 | low/medium/high/very high |
| Quality | 1-4 | low/medium/high/very high |
| Clarity | 1-4 | low/medium/high/very high |
| Significance | 1-4 | low/medium/high/very high |
| Soundness | 1-4 | poor/fair/good/excellent |
| Presentation | 1-4 | poor/fair/good/excellent |
| Contribution | 1-4 | poor/fair/good/excellent |
| Overall | 1-10 | very strong reject → award quality |
| Confidence | 1-5 | low → absolute |

**AutoAD 当前差距：** ReviewerAgent（`06_实验有效性.md:252-276`）仅在高成本/高风险时临时调用，没有硬性门禁。AI-Scientist 的 ensemble review 可直接作为 champion 晋升的硬门禁——只有 review score 超过阈值才能从 `candidate` 晋升为 `champion`。

### 4.4 Multi-Reflection Idea 生成

**源码位置：** `AI-Scientist/generate_ideas.py:14-72`

```python
# 第一轮：生成初始 idea
response = llm_call(idea_first_prompt.format(
    task_description=prompt["task_description"],
    code=experiment_py_source,
    prev_ideas=archive_string,
))

# 反射轮次（最多 N 轮）
for j in range(num_reflections - 1):
    response = llm_call(idea_reflection_prompt.format(idea=response))
    if "I am done" in response.text:
        break
```

**AutoAD 的适配：** 当前 Compact Cycle 是单次 LLM 调用（`03_ResearchCoordinator.md:108-127`）。如果模型输出质量不佳（schema mismatch、逻辑缺失），没有自动纠正流程。建议在 Compact Cycle 输出后增加一个「self-validation」步骤：检查输出是否符合 `CycleDecision` schema，如果不符合则重试（最多 N 次）。

### 4.5 Section-by-Section 论文写作

**源码位置：** `AI-Scientist/perform_writeup.py:130-180`（per_section_tips）

AI-Scientist 的 `per_section_tips` 是一个 Python dict，为每个章节提供结构化写作指南：

```python
per_section_tips = {
    "Abstract": "1. Start with problem statement... 2. Describe method...",
    "Introduction": "1. Establish context... 2. State contribution...",
    "Method": "1. Include mathematical formulation...",
    ...
}
```

**AutoAD 当前差距：** 全计划未提及自动论文生成。如果未来 AutoAD 需要输出研究报告（roadmap 外），AI-Scientist 的节式写作 + per_section_tips + 两轮精修模式是最直接可复用的设计。

---

## 5. 调度与学习层：借鉴 Claw-AI-Lab 的队列调度与进化系统

### 5.1 持久化 FIFO 任务队列

**源码位置：** `Claw-AI-Lab/agent_bridge.py:601-661`

```python
@dataclass
class Task:
    id: str
    project_id: str
    source_layer: str
    target_layer: str
    topic: str
    status: str = "pending"     # pending → assigned → completed / failed
    ...

class TaskQueue:
    name: str
    path: Path                  # 持久化到 JSON 文件
    tasks: list[Task]
    
    def peek_pending(self) -> Task | None:  # 返回第一个 pending 任务
    def assign(self, task_id, agent_id):    # pending → assigned
    def complete(self, task_id):            # assigned → completed
```

**AutoAD 当前差距：** 计划中的实验流程是确定性串行的，没有任务队列。当需要并行运行多个 Attempt 时（如 `cheap batch 2-4 variants`），计划没有定义谁来调度、如何管理并行度、如何持久化任务状态。

**建议（第二阶段）：** 当 AutoAD 需要跨 Session 并行或管理多 Attempt 队列时，直接复用 Claw-AI-Lab 的模式：
- 每个 `experiment_attempt` 创建一个 Task，状态持久化到 JSON
- Coordinator 不再直接调度执行，而是将 Attempt push 到队列
- 一个轻量级 dispatcher（≈ `schedule_idle_agents`）从队列取任务分配给 `ExperimentWorker`

### 5.2 Evolution 学习系统

**源码位置：** `Claw-AI-Lab/evolution.py`

```python
@dataclass
class LessonEntry:
    stage_name: str
    category: str         # system | experiment | writing | analysis | literature | pipeline
    severity: str         # info | warning | error
    description: str
    timestamp: str

HALF_LIFE_DAYS = 30.0    # 30 天半衰期
MAX_AGE_DAYS = 90.0      # 90 天后遗忘

def _time_weight(timestamp_iso: str) -> float:
    age_days = (now - ts).total_seconds() / 86400.0
    if age_days > MAX_AGE_DAYS:
        return 0.0
    return math.exp(-age_days * math.log(2) / HALF_LIFE_DAYS)
```

**AutoAD 的适配：** `ConvergenceMonitor` 可以跟踪每个 Session 的经验教训，用 Claw-AI-Lab 的 6 分类 + 时间衰减机制将 Lessons 注入到 future Session 的 StrategyPolicy 中。当前 StrategyPolicy 的 `DIVERSIFY_AXES` 等是硬编码的静态策略，进化系统可以让策略随运行经验动态调整。

### 5.3 非阻塞式反馈注入

**源码位置：** `Claw-AI-Lab/executor.py:515-568` 和 `prompts.py:154-162`

反馈注入的完整链路：
```
UI → _save_feedback() → feedback_log.jsonl + human_feedback.jsonl
→ 下次阶段执行 → _load_human_feedback() 读取 stamp 文件消费指针
→ PromptManager.for_stage() 注入 "## Human Researcher Feedback" 区块
```

**AutoAD 当前差距：** 全计划未设计人机交互接口。Claw 的「不阻塞当前循环、以下一个决策点为注入时机」的设计，非常适合 AutoAD Coordinator 的决策边界模型（`01_实验Agents大框架.md:310-320`）。

### 5.4 GPU 感知调度

**源码位置：** `Claw-AI-Lab/agent_bridge.py:726-758`

```python
class GpuAllocator:
    def __init__(self, total_gpus: int = 8, gpus_per_project: int = 2):
        self._occupied: set[int] = set()
    
    def can_allocate(self) -> bool:
        return self.available_count() >= self.gpus_per_project
```

**AutoAD 当前差距：** 计划中的 GpuAllocator（`05_ExperimentJob.md:66-80`）只考虑单项目内的单 GPU 互斥。Claw-AI-Lab 的 `gpus_per_project` 模式更接近实际需求——每个实验可能需要 2 或 4 GPU，分配应以「每个实验任务」为单位而非「每个 GPU」。

---

## 6. 总结：AutoAD 各组件的优化映射

| AutoAD 组件 | 当前计划状态 | 参考来源 | 优化方式 | 预估节省/改进 |
|---|---|---|---|---|
| `CognitiveTaskRunner`（01） | 需自建薄接口层 | deepagents SubAgentMiddleware | **删除**，直接使用 SubAgent spec | -8 人天 |
| `Summarization`（04） | 未实现 | deepagents SummarizationMiddleware | 直接使用并配置 per-role 触发策略 | -15 人天 |
| `StrategyPolicy`（07） | 硬编码 overlay | deepagents SkillsMiddleware | 改为 SKILL.md 渐进式加载 | -12 人天 |
| `ContextPack`（03） | FrontView + OutcomeCards | Arbor Constraints Block | **增加** PRUNED LESSONS + VALIDATED FINDINGS | +60 行代码 |
| `ConvergenceDetector`（07） | 只列信号，无算法 | Arbor convergence.py | **增加** 滑动窗口速度 + 父节点耗尽检测 | +150 行代码 |
| `AttemptCategory`（06） | 3 类 | Arbor `needs_retry` | **增加** RETRYABLE_FAILURE | +3 行 enum |
| `Compact Cycle`（03） | 单次 LLM 无自纠正 | AI-Scientist multi-reflection | **增加** self-validation + retry | +30 行代码 |
| `ReviewerAgent`（06） | 仅临时调用 | AI-Scientist ensemble review | **增加** champion 晋升的硬性 review 门禁 | +200 行代码 |
| `Experiment` 适配器 | 需适配每仓库 | AI-Scientist template contract | **增加** `prompt.json` + `seed_ideas.json` 可选加载 | - (简化适配) |
| `ChampionStore`（06） | 单值覆盖 | AiScientist candidate registry | **改为** 不可变快照 + `select_champion()` | +80 行代码 |
| `IOU 平衡监测`（—） | 未设计 | AiScientist reminder system | **新增** implement/evaluation 平衡检测 | +40 行代码 |
| `HITL`（—） | 未设计 | deepagents HITL + Arbor checkpoint HITL | **新增** 关键决策点 interrupt_on + checkpoint | +50 行配置 |
| `任务队列`（—） | 无调度（第二阶段） | Claw-AI-Lab FIFO queue | **预留** TaskQueue 模式供第二阶段 | +100 行代码 |
| `进化学习`（—） | 无（第一阶段） | Claw-AI-Lab evolution | **预留** 时间衰减 lessons → skill overlay | +150 行代码 |
| `文件契约`（01） | 只定义目录结构 | AiScientist constants.py | **增加** Agent/system_prompt 中注入读/写表 | +30 行代码 |

### 核心建议

1. **PR-01A（Session）之前**：锁定 deepagents v0.6.10 为依赖，删除计划中重复的 CognitiveTaskRunner
2. **PR-02A（Idea Tree）中**：增加 `get_constraints_block()`（PRUNED LESSONS + VALIDATED FINDINGS），增加 `needs_retry` 状态
3. **PR-02C（Compact Cycle）中**：增加 self-validation + 最多 3 次重试对齐 Arbor 的 `_classify_executor_outcome()`
4. **PR-05E（Reflection）中**：将 ReviewerAgent 改为 champion 晋升的硬性门禁，采用 AI-Scientist 的 9 维评分
5. **PR-06A（Monitor）中**：增加 IOU 平衡检测 + 滑动窗口速度算法替代当前的空信号列表
6. **PR-06C（Strategy Overlay）中**：改为 deepagents SKILL.md 格式，支持渐进式加载
7. **ChampionStore**：改为 AiScientist 的不可变快照 + 冠军指针模式
8. **全计划中**：预留 HITL 接口（interrupt_on），进化和队列调度模块（第二阶段）

以上 8 项改进总计约 **节省 -35 人天（删掉冗余抽象）+ 新增约 800 行代码（核心模式）**，整体工作量不增反减，同时大幅提升系统的鲁棒性和可扩展性。
