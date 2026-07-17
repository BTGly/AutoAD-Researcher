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

### 2.1 三层 SHA256 防御（参考 AutoSOTA 行为，AutoAD 独立实现 `[REIMPL]`）

> **参考依据：**
> - AutoSOTA `cli_guide.md` 对 protected artifact、违规退出和成绩丢弃的行为描述；
> - 私有流水线 `record_score.sh` 的公开使用痕迹（该脚本不属于开源仓库，不可直接复用）；
> - 实验目录中 Agent 生成的 `record_score.py` 仅作为实现复杂度参考。
>
> **约束：**
> - AutoAD 不依赖 `/tools/record_score.sh`；
> - 不从 AutoSOTA 私有流水线复制代码；
> - ProtectedArtifactGuard 是 AutoAD 自有实现。

AutoAD 实现以下四个函数，不照搬 AutoSOTA 的 `record_score` 完整流水线：

```python
freeze_protected_artifacts(paths) -> ProtectedHashes
verify_protected_artifacts(snapshot) -> HashVerification
classify_protocol_violation(verification) -> bool
invalidate_attempt_metrics(attempt_id, reason) -> None
```

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
   → AttemptCategory.PROTOCOL_VIOLATED
   → 该 attempt 的 metrics 被丢弃，不进入 DecisionEngine
   → 不更新 champion
   → 在日志中列出被篡改的文件
```

AutoAD 不实现：

- `scores.jsonl` 格式（AutoAD 使用自己的 AttemptStore）；
- `record_score.sh` 的 Docker 安装路径逻辑；
- AutoSOTA 特有的 Git tag（如 `_best`）；
- AutoSOTA 特有的 exit code 9 体系（AutoAD 使用 `failure_code=PROTECTED_ARTIFACT_CHANGED`）。
- 自动化 `git checkout` 回退（AutoAD 由 worktree 隔离天然防护）。

**三层的关键设计意图（来自 AutoSOTA）：** 前两层让 Agent **少尝试犯规**，节省 token。第三层让犯规尝试**零回报**，促使自我修正。不是只有代码检查——是一个完整的威慑链。

### 2.2 保护什么 vs 不保护什么（参考 AutoSOTA 原则）

| 锁定 | 不锁定 |
|------|--------|
| eval entrypoint 脚本 | model 训练代码 |
| metric computation 模块 | training config |
| 测试集 / ground truth | 中间输出 / logs |
| held-out 数据 | checkpoint 文件 |
| dataset split 逻辑 | |

### 2.3 复位机制

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

### 3.2 实现有效性（第一版——布尔 Gate）

去掉 VERIFIED/UNVERIFIED/INVALID 三级枚举，改为四个独立布尔 check：

```text
patch_applied   (patch.diff 非空 + allowed_paths 内 + protected SHA256 未变)
smoke_passed    (smoke / import exit code == 0)
metrics_parsed  (metrics.json 存在且符合 schema)
protocol_intact (evaluation contract 未变化：数据集/split/metric 实现 hash)
```

不称为 ACTIVATION_VERIFIED。不宣称「代码已按假设生效」。只说「当前 patch 在当前协议下产生了该指标结果」。

后续反复出现「代码改了但实际没走通」的假阳性后，在具体 Adapter 中加 domain check，不做通用 activation verification。

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
    RUN_FAILED = "run_failed"             # OOM/NaN/timeout/crash → 不比较，由 failure_code 决定重试
    PROTOCOL_VIOLATED = "protocol_violated"   # 改 protected 文件/split/metric → 排除不重试
```

`scientific_effect` 仅在 `SCIENTIFICALLY_EVALUABLE` 时存在：
```text
IMPROVEMENT | NO_EFFECT | REGRESSION | INCONCLUSIVE
```

对于 OOM 的 Attempt：
```json
{ "attempt_category": "run_failed", "failure_code": "OOM", "scientific_effect": null, "retryable": false }
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

## 7. Champion 晋升系统

### 7.1 三层分离架构

命令（Command）、事务（Transaction）、审计事件（Event）三层分离：

```text
Command Layer      → Coordinator 工具接口，V1 仅暴露单一命令
Transaction Layer  → 可恢复的两阶段提交协议，保证原子性
Event Layer        → 不可变审计日志，记录已发生事实
```

### 7.2 CandidateRegistry — 不可变候选快照

每次 `PROMOTE_AND_MERGE` 候选晋升前，先写入不可变 `CandidateSnapshot`：

```python
class CandidateSnapshot(BaseModel):
    candidate_id: str
    evaluation_contract_hash: str
    idea_id: str
    attempt_id: str
    source_commit: str
    patch_sha256: str
    metrics_ref: str
    resource_ref: str
    b_dev_evidence_ref: str
    b_test_evidence_ref: str | None
    created_at: str
```

存储布局：

```text
champions/
├── candidates/
│   ├── candidate_001.json
│   └── candidate_002.json
├── champion_events.jsonl
├── current_by_contract.json
└── transactions/
    ├── tx_<id>.json
    └── ...
```

### 7.3 ChampionEvent — 审计事件

```python
class ChampionEventType(str, Enum):
    PROMOTED_AND_MERGED = "promoted_and_merged"
    ROLLED_BACK = "rolled_back"

    # 仅 schema 预留，V1 Validator 禁止生成
    PROMOTED = "promoted"
    MERGED = "merged"


class ChampionEvent(BaseModel):
    event_id: str
    transaction_id: str
    event_type: ChampionEventType

    evaluation_contract_hash: str
    candidate_id: str
    previous_candidate_id: str | None

    source_branch: str | None
    source_commit: str | None

    trunk_commit_before: str
    trunk_commit_after: str
    merge_commit: str | None
    revert_commit: str | None

    approval_ref: str
    reverts_event_id: str | None = None

    created_at: datetime
```

关键字段说明：

- `previous_candidate_id`：回滚时知道恢复谁
- `trunk_commit_before`：乐观并发检查
- `trunk_commit_after`：恢复时判断 Git 操作是否已完成
- `merge_commit`：`git merge --no-ff` 产生的独立 merge commit，用于可审计回滚
- `reverts_event_id`：明确 ROLLBACK 对应哪次晋升

### 7.4 PromotionTransaction — 可恢复事务协议

```python
class PromotionTransactionStatus(str, Enum):
    PREPARED = "prepared"
    GIT_APPLIED = "git_applied"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class PromotionTransaction(BaseModel):
    transaction_id: str
    command_type: Literal["promote_and_merge", "rollback"]

    evaluation_contract_hash: str
    candidate_id: str
    approval_ref: str

    expected_current_candidate_id: str | None
    expected_trunk_commit: str

    resulting_trunk_commit: str | None = None
    event_id: str | None = None

    status: PromotionTransactionStatus
    created_at: datetime
    updated_at: datetime
```

**V1 提交顺序：**

```text
1. 获取 evaluation_contract_hash 对应的 champion lock

2. 重新验证：
   - candidate 存在
   - B_test 已通过
   - guardrail 通过
   - protected hash 完好
   - current champion 未变化
   - trunk HEAD 等于 expected_trunk_commit

3. 写 PromotionTransaction(status=PREPARED)

4. git merge --no-ff <candidate-branch>
   → 记录 resulting_trunk_commit
   → transaction → GIT_APPLIED

5. 写不可变 CandidateSnapshot

6. 追加 ChampionEvent(PROMOTED_AND_MERGED)

7. 原子替换 current_by_contract.json 中的指针
   （写临时文件 → fsync → os.replace()，不直接覆盖）

8. transaction → COMMITTED

9. 释放 lock
```

**崩溃恢复规则：**

| Transaction 状态 | trunk HEAD 状态 | 恢复动作 |
|---|---|---|
| `PREPARED` | == expected_trunk_commit | 安全重做 |
| `PREPARED` | ≠ expected_trunk_commit | 标记 CONFLICT，禁止自动继续 |
| `GIT_APPLIED` | == resulting_trunk_commit | 补写 Snapshot + Event + pointer → COMMITTED |
| `GIT_APPLIED` | ≠ resulting_trunk_commit | 拒绝自动恢复 |
| `COMMITTED` | 任意 | 幂等，直接返回已有 Event |

**Git merge 策略：** 始终 `git merge --no-ff <candidate-branch>` 产生独立 merge commit，避免无法区分的 fast-forward。

**ROLLBACK 机制：** 不使用 `git reset --hard`（会丢失后续 trunk 提交），而是：

```text
git revert -m 1 <merge_commit>  →  产生 revert_commit
恢复 current champion pointer → previous_candidate_id
追加 ChampionEvent(ROLLED_BACK, reverts_event_id=原事件ID)
```

### 7.5 PromotionApproval — 审批决议独立存储

```python
class PromotionApproval(BaseModel):
    approval_id: str
    candidate_id: str
    mode: Literal["human", "automatic"]
    decision: Literal["approved", "rejected"]
    policy_snapshot_ref: str
    approved_by: str | None
    created_at: datetime
```

`promote_and_merge_candidate` 必须引用 `approval_id`，不能自行判断"似乎已批准"。

### 7.6 批准策略

**默认必须人工确认**（以下任一条件触发 HITL）：

```text
首次 promotion
noise floor 尚未 LOCKED
seed 数不足
delta ≤ 2 × noise_floor
存在任何 guardrail 接近阈值
资源预算剩余 < 10%
B_test 类别表现方向冲突
用户未显式开启 auto_approve
```

**自动批准的必要条件**（全部满足方可 auto-approve）：

```text
用户显式启用 auto_approve
B_test 通过
Candidate 为 SCIENTIFICALLY_EVALUABLE
EvaluationContract hash 一致
protected hash 完好
noise floor 已 LOCKED
满足最低 seed 数
主指标提升 > auto_approve_noise_multiplier × noise_floor
所有 guardrail 通过
无类别发生严重回归
预算足够继续至少一次最小确认实验
```

默认：

```python
auto_approve_noise_multiplier = 2.0
```

"同一 Contract 连续 3 次 promotion 均获人工批准" → UI **建议**用户启用 auto_approve，系统不自作主张切换。

### 7.7 DecisionEngine

先执行确定性 gate：

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
→ 进入 PromotionPolicy → HITL 或 auto-approve
→ PROMOTE_AND_MERGE 原子事务
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

### 7.8 Execution Mode 映射

| Mode | Experiment execution | Promotion / merge |
|---|---|---|
| `plan_only` | 禁止 | 禁止 |
| `approve_each_step` | 每个 Attempt 前确认 | 每次确认 |
| `agent_assisted_after_approval` | 初始计划批准后自动执行 | 默认仍确认；用户显式启用 auto-approve 后可自动 |

---

## 8. “代码错还是思路错”的接受边界

系统不承诺程序化二分。

底层规则：

```text
execution = COMPLETED
+ patch_applied = true
+ smoke_passed = true
+ metrics_parsed = true
+ protocol_intact = true
→ AttemptCategory.SCIENTIFICALLY_EVALUABLE
```

低分表示：

> 当前具体实现未支持假设。

不自动等于：

> 假设被彻底反驳。

如果 patch 未应用或 smoke 失败：

- 不用于 prune 科学方向；
- 优先标记为 repair，不消耗新 GPU attempt。

如果多次 patch/smoke/metrics 均通过但持续回归：

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
- implementation（patch_applied / smoke_passed / metrics_parsed）；

### PR 05C：NoiseFloor / OutcomeCard

- baseline replicates；
- metric-specific；
- category；
- deterministic summary。

### PR 05D：DecisionEngine / CandidateRegistry

- CandidateSnapshot schema；
- ChampionEvent schema；
- PromotionTransaction schema；
- Transaction 两阶段提交协议（PREPARED → GIT_APPLIED → COMMITTED）；
- 崩溃恢复逻辑（PREPARED/GIT_APPLIED/COMMITTED 三种恢复路径）；
- current_by_contract.json 原子写入（临时文件 → fsync → os.replace）；
- B_test gate；
- PromotionPolicy（HITL 与 auto-approve 规则）；
- PromotionApproval 独立存储；
- rollback 机制（git revert -m 1 → pointer 恢复 → ROLLED_BACK 事件）。

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

- B_dev improve → 进入 candidate 但不触发晋升；
- guardrail fail → no promote；
- B_test fail → no promote；
- B_test pass → 进入 PromotionPolicy → HITL 或 auto-approve；
- resource budget fail → HITL 停等；
- duplicate candidate → 拒绝；
- Git merge 成功、pointer 写入前崩溃 → 恢复后补提交；
- pointer 更新失败 → current champion 不产生错误指向；
- 同一 transaction_id 重放 → 不重复 merge；
- trunk HEAD 变化 → promotion conflict；
- B_test 缺失 → 拒绝；
- approval 缺失 → 拒绝；
- PROMOTE_ONLY / MERGE_ONLY 在 V1 中 → 拒绝（Validator 拦截）；
- rollback → 创建 revert commit，不 reset history；
- rollback → pointer 恢复 previous candidate。

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
- Champion 有可验证 lineage（CandidateSnapshot + ChampionEvent 追溯）；
- PromotionTransaction 在任意阶段崩溃后可安全恢复；
- 同一 transaction_id 重放幂等，不重复 merge、不重复追加事件；
- `promote_and_merge_candidate` 必须引用 valid `approval_ref`；
- V1 中 `PROMOTE_ONLY` / `MERGE_ONLY` 被 Validator 拒绝；
- 每个决策区分事实、推断、置信度；
- 失败实验能够影响后续 ideation。
