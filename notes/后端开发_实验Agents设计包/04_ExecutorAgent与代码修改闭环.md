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
expected_activation_evidence
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

### 3.3 SEARCH/REPLACE 策略（直接复用 aider）

> **来源：** `/root/autodl-tmp/repos/aider/aider/coders/search_replace.py`（757 行）。
> 不复写——直接复用 aider 的 `flexible_search_and_replace()` 和 `RelativeIndenter`。

#### 3.3.1 四层策略栈（来自 aider line 565-577）

```python
# 从精确到模糊：任一成功即返回
strategies = [
    (exact_match,       [no_preprocess, strip_blank_lines, normalize_whitespace]),
    (relative_indent,   [no_preprocess]),  # aider 的 RelativeIndenter
    (git_cherry_pick,   [no_preprocess]),  # git merge engine
    (diff_match_patch,  [no_preprocess]),  # Google dmp 行级回退
]
```

**关键细节（来自 aider）：**

| 层 | 机制 | 文件:行号 |
|----|------|----------|
| **Exact match** | 最便宜的字面匹配，3 种预处理组合 | `search_replace.py:565-577` |
| **RelativeIndenter** | 将绝对缩进转为相对缩进（第行相对前行），解决 LLM 的缩进重排问题 | `search_replace.py:18-171` |
| **Git cherry-pick** | O→S→R commits，cherry-pick R 到 O。利用 Git 的 merge engine 处理冲突 | `search_replace.py:448-482` |
| **DMP lines** | Google diff-match-patch，阈值 0.8 | `editblock_coder.py:293` |

#### 3.3.2 Pre-edit dirty_commit（来自 aider `base_coder.py` line 2411-2423）

```text
在 ExecutorAgent 开始修改前:
  1. 如果 worktree 有未提交 change → git add -A && git commit -m "safety: pre-edit checkpoint"
  2. 执行 SEARCH/REPLACE
  3. 如果 smoke/validation 失败 → git reset --hard HEAD~1（回滚到 checkpoint）
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
  → apply (4层策略栈)
  → diff validate
  → syntax/import/smoke
  → metrics parsed
  → finish or bounded repair
```

### 3.4 有界修复与 Gate

#### 3.4.1 PreApplyPatchGate — 修复前验证

ExecutorAgent 的每次 SEARCH/REPLACE 在应用前必须经过 `PreApplyPatchGate`：

```text
检查: 目标文件是否在 allowed_paths 内？
  NO → REPAIR_REJECTED_HARD（拒绝此修复，返回结构化错误给Executor）
检查: 目标文件是否在 forbidden_paths？（包括 protected artifacts、eval scripts）
  NO → REPAIR_REJECTED_HARD
检查: 是否越出 worktree 范围？
  YES → REPAIR_REJECTED_HARD
检查: Patch 是否改变了预期外的算法机制（如修改了 loss function、改变了模型架构）？
  YES → SEMANTIC_DEVIATION
```

#### 3.4.2 PostApplyDiffGuard — 修复后验证

```text
检查: 实际 diff 是否匹配 proposed patch？
  NO → ROLLBACK（git checkout 回退）
检查: protected_paths SHA256 是否变化？
  YES → ROLLBACK + REPAIR_REJECTED_HARD
检查: 修改范围是否超出 InterventionContract 定义的边界？
  YES → SEMANTIC_DEVIATION
```

#### 3.4.3 硬策略违规 vs 语义偏移（明确区分）

**硬策略违规（Hard Policy Violation）：**

- 修改了 `forbidden_path` / `protected artifact` / `eval script`
- 越出了 worktree 范围

**处理：**
- 第一次误触 → 拒绝当前 patch，返回结构化错误，剩余 repair 预算允许时重试
- 同一类别违规重复 ≥ 2 次 → `implementation_failed`

**语义偏移（Semantic Deviation）：**

- 文件路径合法，但改了 loss / mechanism / training objective
- 越出了 InterventionContract 的范围

**处理：** 立即停止 Attempt 内修复，返回 `SEMANTIC_DEVIATION`，由 Coordinator 决定是否创建 child Idea。

#### 3.4.4 允许的修复类型

```text
syntax_error
import_error
shape_error
parameter_not_applied
hook_not_activated
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

### 3.5 Semantic Deviation

如果必须改变：

- mechanism；
- target module 家族；
- loss/objective；
- 未授权参数；
- evaluation protocol；
- dataset split；

Executor 返回：

```text
SEMANTIC_DEVIATION
```

不继续修改。Coordinator 决定 child Idea。

### 3.6 ImplementationStatus（简化版）

第一版不引入 activation evidence verification。遵循 Arbor 的「产生指标 = 实验有效」原则，只做三层确定性检查：

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
