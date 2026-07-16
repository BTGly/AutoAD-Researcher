# 开发计划 05：实验有效性、Reflection 与决策

## 1. 目标

建立“结果是否可信”与“结果意味着什么”之间的明确分层，避免 Coordinator 基于错误指标自我迭代。

---

## 2. EvaluationContract

Session 开始时冻结：

```text
baseline commit
dataset identity
split identity
B_dev / B_test
category set
metric implementation
primary metric
guardrails
aggregation
seed policy
checkpoint selection
time/resource budget
protected artifacts (路径列表 + SHA256)
```

保存 hash。**实验前后检查 protected artifact hash。**

### 2.1 三层 SHA256 防御（直接复用 AutoSOTA）

> **来源：** `/root/autodl-tmp/repos/AutoSOTA/cli_guide.md` lines 735-802；`record_score.sh`。

```
第一层 — SOFT（prompt 注入）
  在 ExecutorAgent 和 Coordinator 的 system prompt 中声明:
  "永远不修改以下受保护文件: {protected_paths}"
  "永远不修改 eval 脚本、测试数据、metric 实现"

第二层 — CONSEQUENCE WARNING（prompt 注入）
  "如果你修改了受保护文件:
   - 实验的 metrics 将被丢弃
   - 该 attempt 标记为 PROTOCOL VIOLATION
   - 不会计入 KEEP/DISCARD"

第三层 — HARD SHA256 CHECK（确定性代码）
  实验前: sha256sum 所有 protected_paths → 写入 protected_hashes.json
  实验后: 重新计算 sha256sum，与 baseline 对比
  → 不匹配 → exit code 9 (PROTOCOL VIOLATION)
  → 该 attempt 的 metrics 被丢弃，不写入 scores.jsonl
  → 不更新 champion
  → 在日志中列出被篡改的文件
  → (可选) git checkout 自动回退改动
```

**三层的关键设计意图（来自 AutoSOTA）：** 前两层让 Agent **少尝试犯规**，节省 token。第三层让犯规尝试**零回报**，促使自我修正。不是只有代码检查——是一个完整的威慑链。

### 2.2 保护什么 vs 不保护什么（来自 AutoSOTA）

| 锁定 | 不锁定 |
|------|--------|
| eval entrypoint 脚本 | model 训练代码 |
| metric computation 模块 | training config |
| 测试集 / ground truth | 中间输出 / logs |
| held-out 数据 | checkpoint 文件 |
| dataset split 逻辑 | |

### 2.3 复位机制（来自 AutoSOTA）

删除 `protected_hashes.json` → 重新 baseline。用于合法场景（如用户显式修改了 eval 协议）。

---

## 3. 四层结果模型

### 3.1 ExecutionStatus

```text
COMPLETED
CRASHED
TIMEOUT
CANCELLED
LOST
```

### 3.2 ImplementationStatus

```text
VERIFIED
UNVERIFIED
INVALID
```

依据：

- patch；
- protected paths；
- activation evidence；
- smoke；
- target parameter；
- expected module。

### 3.3 EvaluationStatus

```text
COMPARABLE
NON_COMPARABLE
```

依据：

- dataset；
- split；
- metrics；
- seed；
- checkpoint；
- command；
- output completeness；
- evaluation hash。

### 3.4 ScientificEffect

```text
IMPROVEMENT
NO_EFFECT
REGRESSION
INCONCLUSIVE
```

只有前三层通过才计算。

### 3.5 AttemptCategory（简化版——三类）

遵循 SWE-Together 的二进制分类思路，扩展为三类：

```python
class AttemptCategory(str, Enum):
    SCIENTIFICALLY_EVALUABLE = "evaluable"   # 正常结束 + metrics 可解析 + 协议完好
    INFRA_FAILED = "infra_failed"             # OOM/NaN/timeout/crash → 不比较，可重试
    PROTOCOL_VIOLATED = "protocol_violated"   # 改 protected 文件/split/metric → 排除不重试
```

`scientific_effect` 仅在 `SCIENTIFICALLY_EVALUABLE` 时存在：
```text
IMPROVEMENT | NO_EFFECT | REGRESSION | INCONCLUSIVE
```

对于 OOM 的 Attempt：
```json
{ "attempt_category": "infra_failed", "failure_code": "OOM", "scientific_effect": null, "retryable": true }
```

不需要 5 级 EvidenceDisposition 或 5 级 OperationalDisposition。

---

## 4. Noise Floor（自适应 3→5→7 渐进策略）

### 4.1 NoiseCalibrationPolicy

由确定性代码决策，Coordinator 可以请求更多 seed 但不能绕过最低要求。

```text
3 seed → PROVISIONAL_NOISE_FLOOR
  - 允许开始探索
  - 边界性提升不能直接晋升 champion
  - 接近 noise threshold 的候选需要补 seed
  - 明显回归仍可提前识别

5 seed → LOCKED（默认锁定量级）
  - 以下情况自动补到 5 seed:
    * candidate 接近 promotion threshold
    * 不同 seed 方向不一致
    * 类别方差差异明显
    * 即将进行高成本确认
    * 当前方差估计不稳定

7 seed → LOCKED_MAX（最大置信度）
  - 用于高价值 champion 的最终确认

若结果范围极小、无异常点、无指标饱和时可用保守方差上界:
  3 seed → LOCKED_LOW_CONFIDENCE（特殊情况）
```

### 4.2 预算上限

Noise Floor 默认预算上限：

```text
min(5 次 baseline run, Session GPU 预算的 10%)
```

预算不足 3 次 baseline → 标记 `UNCALIBRATED`：
- 允许继续探索
- **禁止宣称小幅提升**（所有 delta 需要人工确认）
- Coordinator 可见此状态标记

### 4.3 输出

- baseline 多 seed；
- 同一代码重复运行；
- 按 metric/category 估计波动；
- 保存 mean/std/CI；
- delta 小于阈值时不判定真实改进。

输出：

```text
noise_floor.json
```

规则不能只写死一个全局 2σ；应允许 metric-specific threshold。

---

## 5. OutcomeCard

确定性代码生成给 Coordinator 的压缩状态：

```text
attempt
execution
implementation
evaluation
primary delta
guardrail deltas
noise relation
resource delta
category summary
evidence refs
recommended deterministic gate
```

不直接把完整 stdout 注入 LLM。

---

## 6. Reflection

### 6.1 Coordinator 基础 Reflection

每个 decision boundary 必须输出：

```text
hypothesis_verdict
KEEP-WHY
failure-WHY
confidence
uncertainty
next action
```

### 6.2 ReflectionAgent

触发：

- seed 冲突；
- primary/guardrail 冲突；
- 类别分化；
- 高价值提升；
- 疑似机制不一致；
- 多分支比较；
- inconclusive 持续。

结构化输出：

```text
observed_effect
mechanism_interpretation
alternative_explanations
implementation_concerns
confidence
reusable_property
derived_hypotheses
recommended_tree_action
```

---

## 7. Champion 与 DecisionEngine

ChampionStore 保存：

```text
candidate_id
commit
metric summary
resource summary
validity
B_dev evidence
B_test evidence
promoted_at
```

DecisionEngine 先执行确定性 gate：

```text
invalid
→ reject result

within noise
→ no effect / confirm

primary improves, guardrail violates
→ no promote

B_dev improves sufficiently
→ candidate

B_test gate passes
→ champion
```

Coordinator 负责语义动作：

```text
repair
confirm seed
derive child
pivot
prune
continue
stop proposal
```

---

## 8. “代码错还是思路错”的接受边界

系统不承诺程序化二分。

底层规则：

```text
COMPLETED + VERIFIED + COMPARABLE
→ scientifically evaluable
```

低分表示：

> 当前具体实现未支持假设。

不自动等于：

> 假设被彻底反驳。

如果实现为 UNVERIFIED：

- 不用于 prune 科学方向；
- 优先 repair 或补 activation evidence。

如果多次 VERIFIED 实现均回归：

- Coordinator 可提高“假设不受支持”的置信度；
- 仍保留 evidence 和替代解释。

---

## 9. 开发步骤

### PR 05A：EvaluationContract 与 SHA guard

- schema；
- hash；
- pre/post；
- B_dev/B_test；
- fixture。

### PR 05B：Validity

- execution；
- implementation；
- evaluation；
- output manifest；
- activation evidence 接入。

### PR 05C：NoiseFloor / OutcomeCard

- baseline replicates；
- metric-specific；
- category；
- deterministic summary。

### PR 05D：DecisionEngine / ChampionStore

- candidate；
- confirm；
- promote；
- reject；
- B_test gate。

### PR 05E：Reflection

- Compact fields；
- ReflectionAgent；
- KEEP-WHY；
- cognitive commit 接线。

---

## 10. 检验方案

### 10.1 防作弊/协议测试

- 修改 eval script；
- 修改 split；
- 修改 metric；
- 读取 B_test 进行 ideation；
- 输出错误 checkpoint；
- 预期全部 NON_COMPARABLE/INVALID。

### 10.2 结果分类测试

矩阵覆盖：

```text
completed/failed
verified/unverified
comparable/non-comparable
improve/noise/regress
```

### 10.3 Noise 测试

- baseline seed 波动；
- delta < threshold；
- delta > threshold；
- category-specific noise；
- insufficient repetitions。

### 10.4 Champion 测试

- B_dev improve；
- guardrail fail；
- B_test fail；
- B_test pass；
- resource budget fail；
- duplicate candidate。

### 10.5 Reflection 测试

- 高价值成功输出 KEEP-WHY；
- 回归不直接声称理论反驳；
- conflict 输出 alternative explanations；
- derived hypothesis 关联 parent；
- evidence refs 完整。

### 10.6 验收标准

- 不可信指标不能进入科研结论；
- B_test 不参与常规 ideation；
- noise 内变化不被当作 SOTA；
- Champion 有可验证 lineage；
- 每个决策区分事实、推断、置信度；
- 失败实验能够影响后续 ideation。
