# 开发计划 03：ExecutorAgent 与代码修改闭环

## 1. 目标

实现临时 ExecutorAgent，在独立 git worktree 中把 Idea 转为受控代码修改，并通过有界修复将实现错误与科学方向变化尽量分离。

---

## 2. 核心输入

### InterventionContract

字段：

```text
idea_id
mechanism
hypothesis
target_modules
allowed_paths
forbidden_paths
allowed_parameters
evaluation_invariants
max_repairs
time_budget
```

### WorkspaceSpec

```text
base_commit
worktree_path
branch
protected_hashes
environment_snapshot_ref
```

---

## 3. 实现组件

### 3.1 WorktreeManager

能力：

- 从 baseline/champion commit 创建 worktree；
- 命名可重放；
- 记录 branch/base SHA；
- 清理；
- 保留失败分支配置；
- 防止操作 trunk；
- protected paths hash。

### 3.2 ExecutorAgent

DeepAgents 配置：

- Filesystem 限制到 worktree；
- shell 工具白名单；
- 无网络默认；
- SEARCH/REPLACE 编辑；
- 最大 step/cost/wall；
- attempt 内保留短期 checkpoint；
- attempt 结束销毁。

### 3.3 SEARCH/REPLACE 策略（自行实现，参考 aider 算法模式）

> aider 生产代码的实际策略链（`search_replace.py`）仅 3 个简单策略，共约 100 行。
> RelativeIndenter、git cherry-pick O/S/R、diff-match-patch 只在 benchmark 中使用，生产路径不用。

#### 3.3.1 三个策略（按优先级尝试）

```text
1. perfect_replace          — 按完整行精确匹配，必须只匹配一次
2. missing_leading_whitespace — 只容忍统一的左侧缩进差异，不忽略行内空格
3. try_dotdotdots           — 匹配 before ... after 锚点，锚点组合必须唯一
```

#### perfect_replace（精确行匹配）

```text
seg.segments = whole_lines[0].split(SEARCH)
if len(seg.segments) != 2:  → 0 次或多次匹配，拒绝
# 精确行级替换
```

#### missing_leading_whitespace（左空白容错）

```text
计算 SEARCH 和 REPLACE 中所有非空行的最小公共缩进
min_indent > 0 → 从两侧削减该缩进
对消除空白后的内容做 lstrip() 匹配
要求所有行的空白偏移完全一致
```

#### try_dotdotdots（省略号锚点支持）

```text
以 `...\n` 分割 SEARCH 块
验证 `before` 和 `after` 锚点片段在原文中各只出现一次
确保顺序一致且不跨越多个候选区
中间内容替换为 REPLACE 块
```

#### 3.3.2 Pre-edit dirty_commit（来自 aider `base_coder.py` line 2411-2423）

```text
在 ExecutorAgent 开始修改前:
  1. 如果 worktree 有未提交 change → git add -A && git commit -m "safety: pre-edit checkpoint"
  2. 执行 SEARCH/REPLACE
   3. 如果 smoke/validation 失败 → git reset --hard HEAD（回退到未修改状态）
```

#### 3.3.3 错误反射（来自 aider `base_coder.py` line 2305-2316）

```text
如果 SEARCH/REPLACE 全部策略失败:
  → 不直接报错退出
  → 把文件当前内容（附近相似行）+ failed strategies 送回 LLM
  → LLM 自然纠正 search block
  → 最多 3 次 reflection (aider 的 max_reflections=3)
```

#### 3.3.4 流程

```text
pre-edit dirty_commit
  → inspect
  → propose edit
  → apply（3层策略栈）
  → diff validate
  → syntax/import/smoke
  → metrics parsed
  → finish or bounded repair
```

### 3.4 有界修复与 Gate

#### 3.4.1 PreApplyPatchGate — 修复前验证（仅确定性检查）

ExecutorAgent 的每次 SEARCH/REPLACE 在应用前必须经过 `PreApplyPatchGate`：

```text
检查: 目标文件是否在 allowed_paths 内？
  NO → REPAIR_REJECTED_HARD（拒绝此修复，返回结构化错误给Executor）
检查: 目标文件是否在 forbidden_paths？（包括 protected artifacts、eval scripts）
  NO → REPAIR_REJECTED_HARD
检查: 是否越出 worktree 范围？
  YES → REPAIR_REJECTED_HARD
检查: patch 是否非空？
  NO → REPAIR_REJECTED_HARD
检查: 文件语法/import/smoke 是否通过？
  NO → 触发 bounded repair
```

语义一致性不做代码级硬门。Executor 输出 `implementation_summary`（changed_symbols, possible_contract_deviation, confidence），Coordinator 根据 `InterventionContract + diff summary` 决定：继续、要求修订、或创建 child Idea。

参考：OpenCode `/tools/permissions` 的模式 —— 只检查文件范围、路径白名单、protected SHA 和语法合法性。

#### 3.4.2 PostApplyDiffGuard — 修复后验证（仅确定性检查）

```text
检查: 实际 diff 是否匹配 proposed patch？
  NO → ROLLBACK（git checkout 回退）
检查: protected_paths SHA256 是否变化？
  YES → ROLLBACK + REPAIR_REJECTED_HARD
```

语义一致性不在此层判断。Executor 附加输出 `possible_contract_deviation + confidence`，由 Coordinator 决策。

#### 3.4.3 硬策略违规（仅确定性检查）

**硬策略违规（Hard Policy Violation）：**

- 修改了 `forbidden_path` / `protected artifact` / `eval script`
- 越出了 worktree 范围

**处理：**
- 第一次误触 → 拒绝当前 patch，返回结构化错误，剩余 repair 预算允许时重试
- 同一类别违规重复 ≥ 2 次 → `implementation_failed`

#### 3.4.3 允许的修复类型

```text
syntax_error
import_error
shape_error
parser_error
smoke_failure
bounded_oom_adjustment
```

最多默认 3 次。

每次写：

```text
repair_index
trigger
classification
patch_ref
validation_result
```

超过上限：

```text
implementation_failed
```

### 3.5 ImplementationStatus（简化版）

遵循 Arbor 的「产生指标 = 实验有效」原则，只做三层确定性检查：

```text
PATCH_APPLIED → patch 非空 + 在 allowed_paths 内 + protected SHA256 未变
SMOKE_PASSED  → exit code 0
METRICS_PARSED → metrics.json 存在且符合 schema
```

不称为 `ACTIVATION_VERIFIED`。不宣称「代码已按假设生效」。只说「当前 patch 在当前协议下产生了该指标结果」。

何时加 activation probe：等真实运行中反复出现以下假阳性后，在具体 Adapter 中增加领域检查（如 `AnomalibAdapter.activation_checks()`），不做通用 TrustedProbeRunner。

---

## 4. Artifact

```text
attempt/
├── intervention_contract.json
├── workspace.json
├── idea_brief.md
├── patch.diff
├── changed_files.json
├── repair_log.jsonl
└── executor_summary.json
```

---

## 5. 开发步骤

### PR 03A：WorktreeManager

- create；
- inspect；
- protected hashes；
- cleanup；
- fixture repo。

### PR 03B：Patch protocol

- SEARCH/REPLACE parser；
- apply；
- diff；
- path policy；
- stable signature；
- idempotency。

### PR 03C：ExecutorAgent

- DeepAgents config；
- tools；
- permissions；
- bounded loop；
- result schema。

### PR 03D：Repair classifier 与 activation evidence

- deterministic known errors；
- Agent suggestion；
- hard guard；
- semantic deviation。

### PR 03E：Anomaly Detection adapters

至少支持：

- generic Python；
- anomalib-style config/engine；
- PatchCore-style repo。

---

## 6. 检验方案

### 6.1 单元测试

- SEARCH/REPLACE 精确应用；
- ambiguous match 拒绝；
- forbidden path 拒绝；
- protected hash 改变拒绝；
- trunk 不能修改；
- stable signature 去重；
- repair count；
- semantic deviation。

### 6.2 Fixture 代码任务

1. 修改正常参数并生效；
2. 参数修改但未被 entrypoint 使用；
3. shape bug，第二次 repair 成功；
4. parser bug；
5. 尝试修改 evaluation script；
6. 需要改变算法机制，返回 semantic deviation；
7. 三次修复均失败。

### 6.3 Worktree 隔离

并行创建两个 idea：

- 两个 patch 不互相可见；
- baseline repo clean；
- protected files hash 不变；
- cleanup 不删除 artifact。

### 6.4 激活验证

为 fixture 插入可观察 marker，验证：

- 代码已改但运行路径未触发 → `UNVERIFIED`；
- 路径触发且参数正确 → `VERIFIED`；
- marker 与 contract 不符 → `INVALID`。

### 6.5 Agent 防卡死

- 重复相同 edit；
- 重复相同 failing command；
- 无进展 N step；
- cost/wall 超限；
- `finally` 必须写 executor result。

### 6.6 验收标准

- 每个 Attempt 有独立 worktree；
- Executor 不能修改评估和数据保护区；
- 最多 3 次 repair；
- 修复记录可审计；
- 实现是否生效有证据；
- scientific change 不在原 Attempt 内静默发生；
- baseline/champion 分支始终干净。

---

## 7. 工具调用规范（薄层，不建独立框架）

### 7.1 ToolSpec

工具定义为 `ToolSpec` + handler function（不做 class-per-tool 框架）：

```python
class ToolSpec(BaseModel):
    name: str
    input_model: type[BaseModel]
    readonly: bool
    concurrency_safe: bool = False
    timeout_seconds: float
    retry_on_timeout: bool = False
    permission_policy: str  # "allow_all" | "path_check" | "contract_check"
```

工具是普通函数，通过 `build_tool()` 注册：

```python
async def tree_add_node(input: TreeAddNodeInput, ctx: ToolContext):
    ...

build_tool(
    spec=ToolSpec(name="tree_add_node", input_model=TreeAddNodeInput, ...),
    handler=tree_add_node,
)
```

不实现：每工具一个目录、18 个生命周期方法、UI renderer、Hook manager、自动并发调度器。

### 7.2 三阶段调用管道

```python
async def execute_tool(spec, raw_input, ctx):
    # Phase 1: Validate (no I/O)
    validated = spec.input_model.model_validate(raw_input)

    # Phase 2: Permission
    decision = permission_check(spec, validated, ctx)
    if decision != "allow":
        return ToolError(decision.reason)

    # Phase 3: Execute (I/O) with timeout
    try:
        return await asyncio.wait_for(spec.handler(validated, ctx),
                                      timeout=spec.timeout_seconds)
    except TimeoutError:
        if spec.retry_on_timeout:
            return await asyncio.wait_for(spec.handler(validated, ctx),
                                          timeout=spec.timeout_seconds)
        return ToolError("tool_timeout")
```

不实现：并发安全批处理、自动重试链、多级回退。

### 7.3 双流上下文构建

```python
# 静态上下文——Session 建立后生成一次，固化到 artifact
static_context = {
    "objective": ...,
    "allowed_paths": ...,
    "protected_hashes": ...,
    "evaluation_contract": ...,
}
write_artifact("coordinator_static_context.json", static_context)

# 动态上下文——每个 decision boundary 生成
turn_context = {
    "tree_frontier": ...,
    "latest_outcome_cards": ...,
    "champion": ...,
    "budget_remaining": ...,
    "recent_commits": ...,
}
write_artifact("coordinator_turn_context.json", turn_context)
```

静态上下文可做内存缓存加速（如 `functools.lru_cache`），但 artifact 才是恢复时的权威真源。不要使用进程内 LRU 作为 session 间共享的状态来源。
