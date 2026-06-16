# AutoAD-Researcher 下一阶段开发计划：证据驱动任务确认与真实纵向闭环

> 文档状态：执行版 v2  
> 制定日期：2026-06-16  
> 适用阶段：Step 2.15.1 地基封板之后  
> 配套规范：[AutoAD 任务参数决策与来源协议](./AutoAD_任务参数决策与来源协议.md)  
> 核心目标：**先由证据与用户确认确定 baseline、dataset、metrics 等任务参数，再跑通“真实材料 → 单一 Idea → patch → 真实实验 → 指标 → 有效性 → 报告”的纵向闭环。**

---

## 0. 关键修正

### 0.1 内部 Benchmark 不等于系统默认

团队可以固定一个 PatchCore + MVTec AD 案例，用于：

```text
CI fixture
Demo
Runner 调试
Metrics Parser 调试
Validity Supervisor 回归测试
```

但真实用户任务不能默认使用 PatchCore、MVTec AD、`bottle` 或 image AUROC。

真实任务必须遵循：

```text
读取用户输入和材料
→ 识别候选参数及其来源
→ 展示证据和推荐理由
→ 用户确认
→ 写入正式任务参数
```

### 0.2 AutoAD 可以推荐，但不能替用户决定

该原则适用于：

```text
baseline
dataset
metrics
category
compute budget
evaluation protocol
```

论文中提到的方法、repo 中识别出的模型、历史实验中的配置和系统推荐都只是候选，不能静默成为正式任务参数。

---

## 1. 当前状态

### 1.1 已完成

```text
run_id / runs/{run_id}
ArtifactStore / EventStore
Pydantic schema
StageResult / PipelineResult
PipelineController
Input Intake / Source Manifest
Paper / Repository Reader contracts
Evidence-based Intent Clarifier
Idea protocol / Idea Source Router
DirectIdeaBackend / IdeaGenerator
pytest / verify.sh / GitHub Actions
```

当前确定性 artifact 链：

```text
input_task.yaml
→ source_manifest.json
→ paper_summary.json / repo_summary.json
→ clarified_task.json
→ idea_context.json
→ idea_candidates.json
```

### 1.2 尚未完成

```text
任务参数候选与确认来源的正式协议
真实 PDF 解析
真实代码仓库分析
Single Idea 用户确认
方法迁移判断
动态实验计划
真实 patch / diff
人工审批
受控命令执行
真实指标解析
科研有效性检查
最终实验报告
```

---

## 2. 总体交付链路

```text
Step 3.0  任务参数来源协议 + 内部 Benchmark 锁定与复现
    ↓
Step 3.1  真实 Repository Reader
    ↓
Step 3.2  真实 Paper Reader
    ↓
Step 3.3  Single Idea 确认
    ↓
Step 3.4  Transferability Judge
    ↓
Step 3.5  Dynamic Experiment Planner
    ↓
Step 3.6  Patch Planner + Human Approval
    ↓
Step 3.7  Runner / Sandbox
    ↓
Step 3.8  Metrics Parser + Validity Supervisor
    ↓
Step 3.9  Final Reporter
    ↓
Step 3.10 一键真实纵向 Demo + 回归评测
```

最终 artifact 链：

```text
input_task.yaml
source_manifest.json
paper_summary.json
repo_summary.json
clarified_task.json
idea_context.json
idea_candidates.json
single_idea.json
idea_confirmation.json
transfer_report.json
experiment_plan.json
patch_plan.json
approval.json
patch.diff
run_command.json
stdout.log
stderr.log
metrics.json
validity_report.json
final_report.md
events.jsonl
```

---

# Step 3.0：任务参数来源协议 + 内部 Benchmark 锁定与复现

## 3.0A：任务参数候选与用户确认协议

### 目标

在开始真实实验前，先明确 baseline 等关键参数从哪里来，以及何时才算正式确认。

### baseline 决策顺序

```text
1. 用户明确指定 baseline
2. 从用户提供的 repo / config / 历史实验中识别候选
3. 从论文实验部分识别作者依赖或对比的方法
4. 如果仍不明确，Intent Clarifier 询问用户
5. 用户没有偏好时，系统给出带理由的候选推荐
6. 用户确认后，候选才能成为正式 baseline
```

### 来源语义

```text
候选来源：
  repo_detected
  paper_mentioned
  history_detected
  system_recommended

正式确认来源：
  user_provided
  user_confirmed
```

`paper_mentioned`、`repo_detected` 和 `system_recommended` 不能直接成为最终 baseline 来源。

### 建议 schema

```python
class DecisionCandidate(BaseModel):
    value: str
    source: Literal[
        "repo_detected",
        "paper_mentioned",
        "history_detected",
        "system_recommended",
    ]
    rationale: str
    references: list[ArtifactReference]


class ConfirmedDecision(BaseModel):
    value: str
    source: Literal[
        "user_provided",
        "user_confirmed",
    ]
    evidence: str


class ClarifiedTask(BaseModel):
    # baseline 只保存已确认值
    baseline: str | None = None
    baseline_candidates: list[DecisionCandidate] = []
    baseline_decision: ConfirmedDecision | None = None
```

### 一致性规则

```text
baseline=None
  → baseline_decision 必须为 None
  → baseline_candidates 可以非空

baseline 非空
  → baseline_decision 必须存在
  → baseline_decision.value 必须等于 baseline
  → source 只能是 user_provided / user_confirmed
```

### 待办

- [ ] 新增 `DecisionCandidate` 与 `ConfirmedDecision` schema。
- [ ] 为 `ClarifiedTask` 增加 `baseline_candidates` 与 `baseline_decision`。
- [ ] 保持当前 `baseline` 字段，明确它只表示已确认值。
- [ ] repo 检测结果只生成候选，不自动填写 baseline。
- [ ] 论文中出现的对比方法只生成候选，不自动填写 baseline。
- [ ] 系统推荐必须包含理由、工程成本和 evidence。
- [ ] 用户确认后才写入正式 baseline。
- [ ] 将相同模式逐步推广到 dataset、metrics、category、compute budget 和 evaluation protocol。

### 测试

```text
用户直接提供 baseline → 保留正式值且不询问
repo 检测 baseline → 只生成候选
论文提到 baseline → 只生成候选
系统推荐 baseline → 只生成候选
用户确认候选 → 正式 baseline 写入
baseline 与 decision.value 不一致 → ValidationError
候选缺 evidence → ValidationError
```

### 提交建议

```text
feat: track baseline candidates and user confirmation provenance
```

---

## 3.0B：内部 Benchmark 案例锁定

### 目标

建立一个团队内部、可重复、可回归的真实实验案例。该案例不参与真实用户 baseline 决策。

### 内部案例建议

```text
case_id: internal_patchcore_mvtec_bottle_v1
baseline: PatchCore
implementation: 团队选定并锁定的唯一实现
dataset: MVTec AD
category: bottle
required_metric: image AUROC
attempts: 2
```

这是团队内部测试选择，不是产品默认值。

### 待办

- [ ] 选择唯一 PatchCore 实现并记录选择理由。
- [ ] 锁定 repository URL、branch/tag、完整 commit SHA 和 license。
- [ ] 固定 Python、PyTorch、CUDA 和依赖版本。
- [ ] 固定 MVTec AD `bottle`，记录数据集 license。
- [ ] 固定 seed、backbone、feature layers、输入分辨率和指标口径。
- [ ] 保存 evaluation contract fingerprint。
- [ ] 编写 `docs/internal_benchmark_case.md`。
- [ ] 配置文件明确标记 `scope: internal_benchmark_only`。

### 建议文件

```text
docs/internal_benchmark_case.md
configs/benchmarks/internal_patchcore_mvtec_bottle.yaml
```

### 配置硬约束

```yaml
scope: internal_benchmark_only
must_not_be_used_as_user_default: true
```

### 提交建议

```text
chore: lock internal PatchCore benchmark case
```

---

## 3.0C：Baseline 双跑复现与证据固化

### 目标

人工确认内部 Benchmark 能独立运行并重复产出指标，为后续 Runner 和 Metrics Parser 提供真实样例。

### 待办

- [ ] 在独立实验环境安装 baseline 依赖，不污染 AutoAD Core 环境。
- [ ] 手工运行内部 baseline。
- [ ] 保存 argv、cwd、环境摘要、stdout、stderr、exit code 和原始指标文件。
- [ ] 使用相同 commit/config/evaluation contract 连续运行两次。
- [ ] 第二次运行不得覆盖第一次。
- [ ] 比较两次 required metric 的差值。
- [ ] 生成 `reproducibility_report.json`。
- [ ] 从真实结果制作脱敏 CI fixture。

### 建议目录

```text
scripts/benchmark/
├── bootstrap_environment.sh
├── run_internal_baseline.sh
├── capture_environment.py
├── fingerprint_case.py
└── compare_attempts.py

fixtures/benchmarks/internal_patchcore_mvtec_bottle/
├── attempt_01/
├── attempt_02/
└── reproducibility_report.json
```

### 运行产物

```text
runs/{run_id}/internal_benchmark/
├── attempt_01/
│   ├── case_snapshot.yaml
│   ├── repo_state.json
│   ├── environment.json
│   ├── command.json
│   ├── fingerprints.json
│   ├── stdout.log
│   ├── stderr.log
│   ├── raw_results/
│   └── metrics.json
├── attempt_02/
└── reproducibility_report.json
```

### 复现通过条件

```text
两个 exit code 均为 0
相同 repository commit
相同 case config hash
相同 evaluation contract hash
required metric 均成功解析
指标差值处于配置容差内
```

### CI 边界

普通 CI 不运行：

```text
MVTec AD
GPU
完整训练/推理
外部网络下载
```

普通 CI 只验证：

```text
配置结构
路径和 overwrite guard
fixture 指标解析
fingerprint 稳定性
双跑比较逻辑
失败结果处理
```

### 提交建议

```text
chore: add isolated internal benchmark environment
feat: add reproducible internal baseline runner
test: add internal benchmark reproducibility fixtures
docs: record internal benchmark reproduction results
```

---

# Step 3.1：真实 Repository Reader

## 目标

让现有 `RepositoryReaderBackend` 分析用户提供的真实本地仓库，并生成带证据的 `repo_summary.json`。

内部 PatchCore Benchmark 只作为测试 fixture，不限制真实用户仓库类型。

## 首版范围

```text
本地已存在仓库
只读分析
仓库类型不限于 PatchCore
不自动 clone
不执行任意自由 shell
```

## 待办

- [ ] 新增 `LocalRepositoryReaderBackend`。
- [ ] 校验 repo 路径位于允许 workspace。
- [ ] 读取 commit SHA、dirty 状态和默认分支。
- [ ] 枚举有限深度目录结构，忽略缓存、checkpoint 和大型输出目录。
- [ ] 定位训练、推理、评价入口与配置文件。
- [ ] 从 repo/config 中识别 baseline 候选及 evidence。
- [ ] baseline 识别结果写入候选，不直接成为正式 baseline。
- [ ] 识别 editable paths、protected paths 和 test/evaluation commands。
- [ ] 计算 evaluation script fingerprint。
- [ ] 每个关键结论提供文件路径或行号 evidence。
- [ ] 内部 Benchmark 仓库提供确定性 fixture 测试。

## 验收

- [ ] 可以分析至少一个固定 Benchmark repo 和一个不同结构的最小 fixture repo。
- [ ] 不同模型名不会被强制归类为 PatchCore。
- [ ] 相同 commit 重复读取结果一致。
- [ ] Reader 不修改用户仓库。
- [ ] baseline 候选包含来源与 evidence。

---

# Step 3.2：真实 Paper Reader

## 目标

从用户提供的 PDF 生成结构化 `paper_summary.json`，并识别可能相关的 baseline、dataset 和 metrics 候选。

## 待办

### 确定性文本提取

- [ ] 选择并锁定 PDF 解析器版本。
- [ ] 提取页级文本并保留页码映射。
- [ ] 检测空页、乱码、扫描 PDF 和解析失败。
- [ ] 不在首版引入向量数据库。

### 结构化摘要

- [ ] 输出研究问题、核心方法、组件、数据假设、训练目标和迁移点。
- [ ] 提取论文使用或对比的方法、数据集和指标，标记为 `paper_mentioned`。
- [ ] `paper_mentioned` 只进入候选，不能自动成为正式参数。
- [ ] 每项关键事实引用页码或章节。
- [ ] LLM 输出重新经过 schema 校验。
- [ ] 提供离线 fixture，CI 不调用外部模型。

## 验收

- [ ] 固定 PDF 可重复生成合法摘要。
- [ ] 核心结论可追溯到页码或章节。
- [ ] 论文对比 baseline 不会被自动选中。
- [ ] 解析失败不生成伪摘要。

---

# Step 3.3：Single Idea 与任务参数确认

## 目标

在进入迁移判断前，确保只有一个 active Idea，并且执行所需关键参数已经由用户确认。

## 待办

- [ ] 新增 `SingleIdea` schema。
- [ ] 新增 `IdeaConfirmation`：approve / revise / reject。
- [ ] 首版只允许一个 active Idea。
- [ ] 展示 baseline、dataset、metrics、category、compute budget 和 evaluation protocol 的候选与来源。
- [ ] 明确标记哪些字段已确认、哪些仍缺失。
- [ ] 未确认的 blocking 参数禁止进入 Experiment Planner。
- [ ] revise 产生新版本，不覆盖旧记录。
- [ ] reject 可以进入终止报告。

## Artifact

```text
single_idea.json
idea_confirmation.json
```

---

# Step 3.4：Transferability Judge

## 目标

基于论文、仓库、任务参数和已确认 Idea 判断方法是否值得迁移。

## 输出

```text
run_id
idea_id
decision: high / medium / low / reject / insufficient_information
problem_compatibility
data_assumption_compatibility
label_requirement_compatibility
candidate_insertion_points
repository_evidence
paper_evidence
engineering_risks
scientific_risks
leakage_risks
minimum_validation_experiment
blocking_questions
```

## 待办

- [ ] 先实现 deterministic validity checks。
- [ ] 检查异常标签、数据假设和任务目标兼容性。
- [ ] 检查是否要求修改 protected evaluation path。
- [ ] 检查仓库是否存在可插入位置。
- [ ] LLM backend 只补充语义判断，不能覆盖硬规则拒绝。
- [ ] insufficient information 必须列出缺口。

---

# Step 3.5：Dynamic Experiment Planner

## 目标

根据用户已经确认的 baseline、dataset、metrics 和预算生成真实 `experiment_plan.json`。

## 硬边界

```text
不得从内部 Benchmark 配置继承用户任务参数
不得把候选字段当成已确认字段
缺少 blocking 参数时必须停止
```

## 必须包含

```text
baseline
baseline_decision evidence
method_variant
dataset
category
metrics
control_group
experiment_group
seed
resource_budget
expected_runtime_range
success_criteria
stop_conditions
protected_evaluation_contract
```

## 验收

- [ ] 所有任务参数可追溯到用户提供或用户确认记录。
- [ ] baseline 与 variant 仅在批准变量上不同。
- [ ] evaluation contract 有 fingerprint。
- [ ] 计划可以转换为结构化 Runner 参数。

---

# Step 3.6：Patch Planner + Human Approval

## 待办

- [ ] 生成目标文件、修改目的、依赖、测试、风险和回滚计划。
- [ ] 在临时 worktree/workspace 中生成 patch。
- [ ] 只修改 editable paths。
- [ ] 禁止修改 protected paths。
- [ ] 输出标准 unified diff。
- [ ] approval 绑定 patch SHA256。
- [ ] 未批准时 Runner 拒绝执行。

## Artifact

```text
patch_plan.json
patch.diff
approval.json
```

---

# Step 3.7：Runner / Sandbox

## 待办

- [ ] 只执行结构化白名单命令。
- [ ] 使用独立 workspace，禁止覆盖 baseline 和历史 run。
- [ ] 记录 argv、cwd、环境变量白名单、时间和 exit code。
- [ ] 支持 timeout、cancel 和失败状态。
- [ ] 保存完整 stdout/stderr。
- [ ] 保存 repo commit、patch hash、config hash 和环境摘要。
- [ ] 首版可使用 subprocess，暂不引入 Temporal。

---

# Step 3.8：Metrics Parser + Validity Supervisor

## Metrics Parser

- [ ] 从真实结果文件或 stdout 解析用户确认的指标。
- [ ] 保存原始值、来源文件、规则和单位。
- [ ] 解析失败显式标记，不允许默认填 0。
- [ ] baseline 与 variant 使用同一 parser。

## Validity Supervisor

- [ ] 检查 dataset split 是否一致。
- [ ] 检查 test label/mask 是否进入训练。
- [ ] 检查 evaluation script 与 protocol 是否变化。
- [ ] 检查后处理和指标口径是否变化。
- [ ] 检查 patch 是否与批准版本一致。
- [ ] 检查指标是否来自真实执行。
- [ ] 检查是否凭单次结果过度宣称。

---

# Step 3.9：Final Reporter

## 原则

```text
无论成功、失败、拒绝还是停止，都生成报告。
所有数字来自 artifact，不由 LLM 自由生成。
LLM 只允许润色已有事实。
```

## 内容

```text
任务和用户确认参数
参数来源与证据
材料和版本
已确认 Idea
迁移判断
实验计划
patch 和审批
环境与命令
指标
有效性检查
可支持和不可支持的结论
下一步建议
artifact 索引
```

---

# Step 3.10：一键真实纵向 Demo + 回归评测

## 双入口

```text
内部 Benchmark Demo
  使用明确标记的固定 PatchCore 案例

真实用户任务
  必须经过参数候选识别与用户确认
```

内部 Demo CLI 可以是：

```bash
uv run autoad run-internal-benchmark \
  --run-id run_internal_patchcore_001 \
  --case configs/benchmarks/internal_patchcore_mvtec_bottle.yaml
```

真实用户 CLI 不得默认填充 baseline：

```bash
uv run autoad run \
  --run-id run_user_001 \
  --task input_task.yaml
```

---

## 4. 测试与评测策略

### 三层测试

```text
单元测试
  schema、provenance、validator、parser、路径和命令边界

集成测试
  artifact → stage → artifact
  使用 fixture，不依赖 GPU 和外部模型

真实案例测试
  内部固定 Benchmark
  允许较慢，不进入普通 CI
```

### 最小 AD-AgentBench

```text
Case 1：用户明确指定 UniAD，系统不得改成 PatchCore
Case 2：上传 PatchCore repo，只生成 repo_detected 候选
Case 3：论文对比 PaDiM/PatchCore，不能自动选择 baseline
Case 4：用户无偏好，系统推荐候选并等待确认
Case 5：patch 尝试修改 evaluation script，被拒绝
Case 6：实验失败，仍生成失败报告
Case 7：指标提升但 evaluation fingerprint 改变，判定无效
```

---

## 5. 近期优先级

### P0：立即执行

```text
Step 3.0A 参数来源与确认 schema
Step 3.0B 内部 Benchmark 案例锁定
Step 3.0C 内部 Baseline 双跑复现
Step 3.1  真实 Repository Reader
Step 3.2  真实 Paper Reader
Step 3.3  Single Idea 与任务参数确认
Step 3.4  Transferability Judge
```

### P0：随后完成闭环

```text
Step 3.5 Experiment Planner
Step 3.6 Patch + Approval
Step 3.7 Runner
Step 3.8 Metrics + Validity
Step 3.9 Report
Step 3.10 End-to-end Demo
```

### P1：真实闭环之后

```text
SQLite 元数据仓储
简单 Web UI
历史 run 浏览
模型调用与成本统计
```

### P2：数据库稳定之后

```text
多 Agent Idea
多个候选去重和选择
多分支低成本 smoke
历史失败经验检索
```

### P3：出现真实需求后

```text
MinIO / S3
PostgreSQL
Temporal / LangGraph
多 worker / 多 GPU
多用户权限
```

---

## 6. 开发节奏

每个 Step：

```text
1. 明确目标与非目标
2. 定义 schema 和来源语义
3. 写 fixture 与失败用例
4. 实现最小功能
5. 运行相关测试
6. 运行全量 pytest
7. 运行 verify.sh
8. 运行真实案例
9. 更新 README / docs / notes
10. 单一职责 commit
```

每步必须回答：

```text
参数来自哪里？
它是候选还是已确认事实？
用户是否拥有最终确认权？
失败是否留下 artifact 和 event？
结论是否来自真实材料或实验？
内部 Benchmark 是否被错误泄漏为产品默认？
```

---

## 7. 建议提交序列

```text
feat: track baseline candidates and user confirmation provenance
chore: lock internal PatchCore benchmark case
chore: add isolated internal benchmark environment
feat: add reproducible internal baseline runner
test: add internal benchmark reproducibility fixtures
docs: record internal benchmark reproduction results
feat: add local repository reader
feat: add real PDF paper reader
feat: add single idea and task parameter confirmation
feat: add transferability judge
feat: add dynamic experiment planning
feat: add patch planning and approval
feat: add controlled experiment runner
feat: add metrics parsing and validity checks
feat: add deterministic final reporting
feat: add end-to-end user flow and internal benchmark command
```

---

## 8. Step 3.x 完成定义

- [ ] 内部 Benchmark 明确标记为 internal-only。
- [ ] 真实用户任务不预设 baseline、dataset、metric 或 category。
- [ ] 候选参数包含来源、理由和 evidence。
- [ ] 正式参数只来自 user_provided / user_confirmed。
- [ ] 使用用户确认的真实 repo 和数据集。
- [ ] 真实读取论文或接受明确用户 Idea。
- [ ] 用户确认唯一 active Idea。
- [ ] 生成有证据的迁移判断。
- [ ] 生成可执行实验计划。
- [ ] 生成并批准最小 patch。
- [ ] 在受控环境运行 baseline 和 variant。
- [ ] 从真实输出解析用户确认的指标。
- [ ] 检查评价协议和数据泄漏风险。
- [ ] 无论结果如何都生成最终报告。
- [ ] 完整 artifact 和 events 可审计。

---

## 9. 一句话路线

> **先用证据识别 baseline、dataset、metrics 等候选并让用户确认；再把一个已确认 Idea 在用户选择的真实实验底座上跑完。PatchCore + MVTec AD 只保留为团队内部 Benchmark，不是系统默认。**