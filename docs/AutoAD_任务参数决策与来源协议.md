# AutoAD-Researcher 任务参数决策与来源协议

> 文档状态：规范版 v1  
> 制定日期：2026-06-16  
> 适用范围：baseline、dataset、metrics、category、compute budget、evaluation protocol 等影响实验决策的任务参数。

---

## 1. 核心边界

> **AutoAD 可以读取证据、识别候选并给出推荐，但不能替用户决定实验任务的关键参数。**

系统必须区分：

```text
候选事实
  从 repo、论文、历史实验或系统规则中识别出来，尚未得到用户确认。

正式任务参数
  用户明确提供，或用户从候选中确认后写入正式任务。
```

因此：

```text
repo_detected
paper_mentioned
history_detected
system_recommended
```

都不能直接成为最终 baseline、dataset、metric、category、compute budget 或 evaluation protocol。

---

## 2. 参数决策顺序

所有关键参数统一采用以下顺序：

```text
1. 用户明确提供
2. 从用户提供的 repo / config / 历史实验中识别候选
3. 从论文中识别相关方法、数据集、指标或对比项
4. 若仍不明确，由 Intent Clarifier 询问用户
5. 用户没有偏好时，系统给出带理由和证据的候选推荐
6. 用户确认后，候选才能成为正式任务参数
```

### 2.1 baseline 示例

```text
用户：我想在我们的 UniAD 模型上加入这个模块
→ baseline = UniAD
→ source = user_provided
→ 不再询问

用户上传了一个 PatchCore repo
→ candidate = PatchCore
→ source = repo_detected
→ 向用户确认，不自动写入 baseline

论文实验中对比了 PaDiM、PatchCore
→ candidates = PaDiM / PatchCore
→ source = paper_mentioned
→ 这些是论文对比方法，不等于用户实施 baseline

用户没有 repo、历史实验或偏好
→ 系统可推荐 PatchCore / PaDiM / FastFlow
→ source = system_recommended
→ 给出代码可用性、资源成本和任务适配理由
→ 由用户选择
```

---

## 3. 来源类型

```python
DecisionCandidateSource = Literal[
    "repo_detected",
    "paper_mentioned",
    "history_detected",
    "system_recommended",
]

DecisionConfirmationSource = Literal[
    "user_provided",
    "user_confirmed",
]
```

关键约束：

```text
候选来源与确认来源必须分开建模。
```

不建议把所有来源都放进一个 `baseline_source` 字段，因为：

```json
{
  "baseline": "PatchCore",
  "baseline_source": "paper_mentioned"
}
```

会错误表达“论文提到 PatchCore，因此系统已经决定使用 PatchCore”。

---

## 4. 建议数据结构

为兼容当前 `ClarifiedTask.baseline` 字段，建议新增候选和确认元数据，而不是改变 `baseline` 的含义。

```python
class DecisionCandidate(BaseModel):
    value: str
    source: DecisionCandidateSource
    rationale: str
    references: list[ArtifactReference]


class ConfirmedDecision(BaseModel):
    value: str
    source: DecisionConfirmationSource
    evidence: str


class ClarifiedTask(BaseModel):
    # baseline 只保存已经确认的正式值
    baseline: str | None = None

    # 未确认候选
    baseline_candidates: list[DecisionCandidate] = []

    # baseline 非空时必须存在，且 value 必须等于 baseline
    baseline_decision: ConfirmedDecision | None = None
```

相同结构可逐步应用于：

```text
dataset_candidates / dataset_decision
metric_candidates / metric_decisions
category_candidates / category_decision
compute_budget_candidates / compute_budget_decision
evaluation_protocol_candidates / evaluation_protocol_decision
```

首版可以先实现 baseline，再复用同一通用模型扩展其他字段。

---

## 5. ClarifiedTask 一致性规则

### 5.1 baseline 未确认

```text
baseline = None
baseline_decision = None
baseline_candidates 可以非空
```

### 5.2 用户直接提供

```text
baseline = UniAD
baseline_decision.source = user_provided
baseline_decision.value = UniAD
```

### 5.3 用户确认候选

```text
baseline_candidates 包含 PatchCore(repo_detected)
baseline = PatchCore
baseline_decision.source = user_confirmed
baseline_decision.value = PatchCore
```

### 5.4 禁止状态

```text
baseline 非空但 baseline_decision 为空
baseline 与 baseline_decision.value 不一致
baseline_decision.source = repo_detected
baseline_decision.source = paper_mentioned
baseline_decision.source = system_recommended
```

---

## 6. Intent Clarifier 行为

Intent Clarifier 应当：

```text
读取用户已有值
读取 repo / config / paper / history 证据
生成候选列表
标明每个候选的来源、理由和 evidence
只对未确认字段提问
等待用户确认
```

Intent Clarifier 不应：

```text
看到 PatchCore repo 就直接写 baseline=PatchCore
看到论文对比 PaDiM 就直接写 baseline=PaDiM
根据系统内部偏好静默选择某个 baseline
自动填充 dataset、metric、category 或 compute budget
```

---

## 7. 内部 Benchmark 与真实用户任务的区别

### 7.1 内部 Benchmark

团队可以固定：

```text
PatchCore
MVTec AD bottle
image AUROC
固定 commit / config / seed
```

用途仅限：

```text
CI fixture
Demo
Runner 调试
Metrics Parser 调试
Validity Supervisor 测试
回归 benchmark
```

### 7.2 真实用户任务

真实用户任务必须重新执行参数决策流程：

```text
读取用户输入和材料
→ 识别候选
→ 展示证据与推荐理由
→ 用户确认
→ 写入正式任务参数
```

内部 Benchmark 不能成为：

```text
系统默认 baseline
系统默认 dataset
系统默认 category
系统默认 metric
```

---

## 8. 其他参数的同类规则

### dataset

- repo config 中出现的数据集只能作为 `repo_detected` 候选；
- 论文使用的数据集只能作为 `paper_mentioned` 候选；
- 用户确认后才能成为正式 dataset。

### metrics

- 论文报告的指标不等于用户必须采用的指标；
- 系统可根据任务类型推荐 AUROC、AUPR、F1、PRO 等；
- 最终指标与评价口径必须由用户确认。

### category

- 内部 Demo 可固定 `bottle`；
- 真实任务应来自用户数据、repo config 或用户确认。

### compute budget

- 系统可以根据环境信息给出成本估计；
- 不能自动假设用户拥有单卡、多卡或特定显存。

### evaluation protocol

- 可以从 repo 和论文中提取候选协议；
- 最终协议必须被确认并生成 fingerprint；
- 后续实验不得静默修改。

---

## 9. 测试要求

至少覆盖：

```text
用户直接提供 baseline → 正式值保留且不询问
repo 检测 baseline → 只生成候选，不自动选择
论文提到 baseline → 只生成候选，不自动选择
系统推荐 baseline → 只生成候选，不自动选择
用户确认候选 → 正式 baseline 写入
baseline 与 decision.value 不一致 → ValidationError
候选缺少 evidence → ValidationError
未确认 baseline 时不能进入需要 baseline 的 Experiment Planner
```

---

## 10. 规范表述

项目文档中推荐使用：

> baseline 优先使用用户明确指定或用户现有代码仓库中的实现；从 repo、论文和历史实验识别出的结果只作为候选并展示证据；若仍不明确，由 Intent Clarifier 询问用户；用户无偏好时，系统可以根据任务适配性、代码可用性和资源预算推荐候选，但最终选择必须由用户确认。团队内部 Benchmark 可固定 PatchCore 等实现，但不得成为真实任务的系统默认逻辑。
