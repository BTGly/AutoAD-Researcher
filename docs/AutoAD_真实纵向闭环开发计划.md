# AutoAD-Researcher 下一阶段开发计划：单一 Idea 真实纵向闭环

> 文档状态：执行版 v1  
> 制定日期：2026-06-16  
> 适用阶段：Step 2.15.1 地基封板之后  
> 核心目标：**停止继续扩展通用抽象，用一个固定案例跑通“真实材料 → 单一 Idea → patch → 真实实验 → 指标 → 有效性 → 报告”的纵向闭环。**

---

## 0. 当前状态与下一步结论

### 0.1 已完成

当前仓库已经具备确定性控制地基：

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

当前 artifact 链已经能够稳定生成：

```text
input_task.yaml
→ source_manifest.json
→ paper_summary.json / repo_summary.json
→ clarified_task.json
→ idea_context.json
→ idea_candidates.json
```

但目前仍缺少以下真实能力：

```text
真实 PDF 解析
真实代码仓库分析
方法迁移判断
动态实验计划
真实 patch / diff
人工审批
受控命令执行
真实 AUROC 等指标解析
科研有效性检查
最终实验报告
```

### 0.2 立即执行的下一步

**先做 Step 3.0：固定 MVP 案例与 baseline 复现。**

在此步骤完成前，不开始数据库、对象存储、Temporal、多 Agent、Web UI 或模型路由扩建。

---

## 1. 第一条真实闭环的固定范围

第一条纵向闭环只支持一个 active Idea，固定范围如下：

```text
任务方向：视觉异常检测 / 工业缺陷检测
baseline：PatchCore
数据集：MVTec AD 单一类别
推荐首个类别：bottle
代码仓库：只选择一个公开实现，并锁定 commit SHA
Idea：只推进一个经用户明确确认的 Idea
实验规模：单机、单类别、小规模 smoke / benchmark
主要指标：image-level AUROC；条件允许再增加 pixel AUROC
```

### 1.1 两种允许的入口

```text
模式 A：用户给出明确实现方案
  → 直接进入 Single Idea 确认和迁移判断

模式 B：用户提供固定论文 PDF
  → 系统提取一个主要可迁移 Idea
  → 用户确认后继续
```

### 1.2 当前明确不做

```text
多 Agent 自由讨论
同时维护多个候选 Idea
候选投票与多分支并行实验
自动搜索最新论文
自动生成复杂 ablation
数据库 / PostgreSQL
对象存储 / MinIO / S3
LangGraph / Temporal 重写
多用户权限系统
完整 Web 产品
```

---

## 2. 总体交付链路

```text
Step 3.0  MVP 案例锁定与 baseline 复现
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

## 3. Step 3.0：固定 MVP 案例与 baseline 复现

### 3.0.1 目标

在 AutoAD 自动化之前，先人工确认实验底座能够独立运行并重复产出指标。

### 3.0.2 待办

- [ ] 选择唯一 PatchCore 实现；优先使用团队已经跑通过的仓库，否则选择维护状态清晰、可固定版本的公开实现。
- [ ] 记录仓库 URL、branch、commit SHA 和 license。
- [ ] 固定 Python、PyTorch、CUDA、依赖版本。
- [ ] 固定 MVTec AD 类别，默认 `bottle`。
- [ ] 固定 baseline 配置、随机种子、输入分辨率和指标口径。
- [ ] 手工运行 baseline，保存完整命令、stdout、stderr、耗时和指标。
- [ ] 连续运行两次，确认命令可重复且输出位置稳定。
- [ ] 保存 evaluation script 的 SHA256，后续禁止静默修改。
- [ ] 编写 `docs/mvp_case.md`，记录固定案例和实验协议。
- [ ] 新增最小 fixture，供 CI 在无数据集/GPU环境下验证命令组装和指标解析。

### 3.0.3 建议新增文件

```text
docs/mvp_case.md
configs/mvp/patchcore_mvtec_bottle.yaml
fixtures/mvp/baseline_stdout.txt
fixtures/mvp/baseline_metrics.json
scripts/run_mvp_baseline.sh
```

### 3.0.4 验收标准

- [ ] 一条人工命令可以重复运行 baseline。
- [ ] 真实指标来自固定 evaluation script。
- [ ] 第二次运行不会覆盖第一次结果。
- [ ] repo commit、环境和配置可追溯。
- [ ] 无 GPU 的 CI 可以使用 fixture 完成非执行类测试。

### 3.0.5 阻塞规则

如果 baseline 不能稳定复现，**暂停后续 Agent 开发，优先修复实验环境**。

---

## 4. Step 3.1：真实 Repository Reader

### 4.1 目标

把现有 `RepositoryReaderBackend` contract 接到一个真实本地仓库分析实现，生成可信的 `repo_summary.json`。

### 4.2 实现范围

首版只支持：

```text
本地已存在的仓库目录
只读分析
固定 PatchCore 仓库
不自动 clone
不执行任意 shell
```

### 4.3 待办

- [ ] 新增 `LocalRepositoryReaderBackend`。
- [ ] 校验仓库根目录必须在允许的 workspace 下。
- [ ] 读取并记录当前 commit SHA、dirty 状态和默认分支。
- [ ] 枚举有限深度目录结构，忽略 `.git`、缓存、checkpoint 和大文件目录。
- [ ] 定位训练、推理、评价入口和主要配置文件。
- [ ] 定位 PatchCore model、feature extraction、memory bank、scoring 相关代码。
- [ ] 识别可修改路径和 protected paths。
- [ ] 记录 test / smoke / evaluation 命令。
- [ ] 计算 evaluation script fingerprint。
- [ ] 对每个结论生成文件路径或行号级 evidence。
- [ ] 将真实结果写入现有 `RepositorySummary`，不新增平行 schema。

### 4.4 建议文件

```text
src/autoad_researcher/readers/local_repository.py
tests/test_local_repository_reader.py
fixtures/repos/patchcore_mini/
```

### 4.5 验收标准

- [ ] 对固定 repo 可稳定生成 `repo_summary.json`。
- [ ] 相同 commit 重复读取结果确定性一致。
- [ ] evaluation script fingerprint 正确。
- [ ] protected paths 至少包含固定 evaluation 入口。
- [ ] 所有 evidence 指向真实存在的文件或行范围。
- [ ] Reader 不修改仓库文件。

---

## 5. Step 3.2：真实 Paper Reader

### 5.1 目标

从一篇固定 PDF 生成结构化、带证据定位的 `paper_summary.json`。

### 5.2 两阶段实现

#### 3.2A：确定性 PDF 文本提取

- [ ] 选择一个 PDF 解析器并锁定版本。
- [ ] 提取页级文本，保留页码映射。
- [ ] 生成 `paper_text.json` 或受控中间文件。
- [ ] 检测空页、乱码、扫描 PDF 和解析失败。
- [ ] 不在本步骤做向量数据库或 RAG。

#### 3.2B：结构化摘要

- [ ] 实现真实 `PaperReaderBackend`，输入页级文本。
- [ ] 输出研究问题、核心方法、组件、数据假设、训练目标、指标和迁移点。
- [ ] 每项关键事实必须引用页码或章节。
- [ ] LLM 输出必须重新经过 `PaperSummary.model_validate()`。
- [ ] 记录模型、prompt 版本、token、耗时和缓存命中信息。
- [ ] 提供离线 fixture backend，CI 不调用外部模型。

### 5.3 建议文件

```text
src/autoad_researcher/readers/pdf_text.py
src/autoad_researcher/readers/llm_paper.py
src/autoad_researcher/model/client.py
src/autoad_researcher/model/prompts/paper_reader.md
tests/test_pdf_text_reader.py
tests/test_llm_paper_reader.py
fixtures/papers/sample_pages.json
```

### 5.4 验收标准

- [ ] 固定 PDF 可重复生成合法 `paper_summary.json`。
- [ ] 核心结论可追溯到页码或章节。
- [ ] 解析失败有明确错误，不生成伪摘要。
- [ ] 模型不得填充原文中不存在的实验事实。
- [ ] 离线 CI 使用固定响应 fixture。

---

## 6. Step 3.3：Single Idea 确认

### 6.1 目标

把现有 `idea_candidates.json` 收缩为一个用户确认的 active Idea，形成后续流程唯一输入。

### 6.2 待办

- [ ] 新增 `SingleIdea` schema。
- [ ] 新增 `IdeaConfirmation` schema：`approve / revise / reject`。
- [ ] 首版只允许一个 active Idea。
- [ ] 支持从 `direct_user_idea` 选择唯一候选。
- [ ] 支持从固定论文摘要提取一个主要候选，再等待用户确认。
- [ ] 未批准时禁止进入 Transferability Judge。
- [ ] revise 必须产生新版本，不覆盖旧确认记录。
- [ ] reject 后流程可生成终止报告，而不是静默结束。

### 6.3 Artifact

```text
single_idea.json
idea_confirmation.json
```

### 6.4 验收标准

- [ ] 一个 run 同时只有一个 active Idea。
- [ ] approval 状态可审计。
- [ ] 未批准 Idea 无法进入后续 stage。
- [ ] 修改历史不被覆盖。

---

## 7. Step 3.4：Transferability Judge

### 7.1 目标

基于论文、仓库、任务和已确认 Idea，判断该方法是否值得迁移到固定 PatchCore 案例。

### 7.2 输出 schema

建议新增 `TransferReport`：

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

### 7.3 待办

- [ ] 先实现 rule-based validity checks。
- [ ] 检查训练阶段是否需要异常标签。
- [ ] 检查是否依赖与当前任务冲突的数据假设。
- [ ] 检查 proposal 是否要求修改 protected evaluation path。
- [ ] 检查仓库中是否存在可插入位置。
- [ ] 再接 LLM backend 补充语义判断。
- [ ] LLM 不得覆盖 deterministic rule 的硬拒绝结果。
- [ ] insufficient information 必须列出具体缺口。

### 7.4 验收标准

- [ ] 明显不兼容的方法能够被拒绝。
- [ ] 每个判断有 paper/repo evidence。
- [ ] 没有足够证据时返回 insufficient，而不是猜测。
- [ ] `reject` 仍能进入 Final Reporter 生成终止报告。

---

## 8. Step 3.5：Dynamic Experiment Planner

### 8.1 目标

替换 `SimplePipelineHarness` 的固定占位计划，生成针对当前案例的真实 `experiment_plan.json`。

### 8.2 必须包含

```text
baseline
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

### 8.3 待办

- [ ] 从 `repo_summary.json` 和 `transfer_report.json` 构造计划。
- [ ] baseline、dataset 和 metrics 必须来自已确认事实。
- [ ] 生成 baseline 与 variant 两组命令模板。
- [ ] 固定相同数据划分、seed 和 evaluation script。
- [ ] 计划必须能映射到后续 Runner 参数。
- [ ] 输出无法执行时应失败，不生成“看起来合理”的计划。

### 8.4 验收标准

- [ ] 计划可以直接转换为命令和配置。
- [ ] baseline 与 variant 只在批准的变量上不同。
- [ ] evaluation contract 有 fingerprint。
- [ ] success / stop 条件可程序化判断。

---

## 9. Step 3.6：Patch Planner + Human Approval

### 9.1 目标

生成最小可审计 patch，并在人类批准前禁止修改真实仓库。

### 9.2 待办

- [ ] 定义 `PatchPlan` 的真实字段：目标文件、修改目的、依赖、测试、风险和回滚。
- [ ] 在临时 worktree 或复制 workspace 中生成 patch。
- [ ] 只允许修改 `editable_paths`。
- [ ] 禁止修改 `protected_paths`。
- [ ] 输出标准 unified diff：`patch.diff`。
- [ ] 记录 changed files、行数和依赖变化。
- [ ] 新增 `ApprovalDecision`：approve / revise / reject。
- [ ] 未批准时 Runner 必须拒绝执行。
- [ ] 批准对象必须绑定 patch SHA256，防止批准后 patch 被替换。

### 9.3 Artifact

```text
patch_plan.json
patch.diff
approval.json
```

### 9.4 验收标准

- [ ] patch 仅修改允许文件。
- [ ] patch hash 与 approval 一致。
- [ ] 人工拒绝时不会执行任何命令。
- [ ] 可在临时 worktree 中干净应用和回滚。

---

## 10. Step 3.7：Runner / Sandbox

### 10.1 目标

在受控环境中执行 baseline 与 variant，保存完整证据。

### 10.2 待办

- [ ] 定义 command whitelist，不接受任意自由 shell。
- [ ] 只执行 `ExperimentPlan` 生成的结构化命令。
- [ ] 使用独立 worktree / workspace，禁止覆盖 baseline。
- [ ] 记录命令、cwd、环境变量白名单、开始/结束时间和 exit code。
- [ ] 支持 timeout、cancel 和失败状态。
- [ ] 保存 stdout / stderr，不只保留最后几行。
- [ ] 保存 GPU/CPU/RAM 基本信息。
- [ ] 保存代码 commit、patch hash、配置 hash。
- [ ] 首版可以使用 subprocess；暂不引入 Temporal。
- [ ] 条件允许时增加 Docker/conda 隔离，但不作为第一提交前置条件。

### 10.3 Artifact

```text
run_command.json
environment.json
stdout.log
stderr.log
execution_result.json
```

### 10.4 验收标准

- [ ] baseline 与 variant 均可独立执行。
- [ ] 失败能被记录并进入报告流程。
- [ ] 命令超时可终止。
- [ ] 不允许删除数据集或访问工作区外路径。
- [ ] 重跑不会覆盖历史结果。

---

## 11. Step 3.8：Metrics Parser + Validity Supervisor

### 11.1 Metrics Parser

- [ ] 从真实输出文件或 stdout 中解析 image AUROC。
- [ ] 条件允许时解析 pixel AUROC、PRO、耗时和显存。
- [ ] 保存原始值、来源文件、解析规则和单位。
- [ ] 解析失败必须显式标记，不允许默认填 0。
- [ ] baseline 与 variant 使用同一 parser。

### 11.2 Validity Supervisor

必须检查：

- [ ] dataset split 是否一致。
- [ ] 是否使用 test label / ground-truth mask 参与训练。
- [ ] 是否修改 evaluation script。
- [ ] 是否改变后处理或指标口径。
- [ ] 是否只挑有利类别。
- [ ] 是否覆盖或删除失败实验。
- [ ] 是否仅凭单次随机结果宣称有效。
- [ ] 指标是否来自真实执行。
- [ ] patch 是否与批准版本一致。

### 11.3 Artifact

```text
metrics.json
validity_report.json
```

### 11.4 验收标准

- [ ] 指标能追溯到真实文件和命令。
- [ ] evaluation fingerprint 不一致时结论无效。
- [ ] validity 不通过时，报告不得宣称方法提升。
- [ ] 失败和无效实验仍被完整保留。

---

## 12. Step 3.9：Final Reporter

### 12.1 目标

无论成功、失败、拒绝还是停止，都生成一份可追溯报告。

### 12.2 报告内容

```text
任务与用户约束
材料和版本
已确认 Idea
迁移判断
实验计划
patch 摘要与批准记录
执行环境和命令
baseline / variant 指标
有效性检查
失败原因或限制
可以支持的结论
不能支持的结论
下一步建议
artifact 索引
```

### 12.3 待办

- [ ] 先实现 deterministic Markdown reporter。
- [ ] 所有数字从 artifact 读取，不从 LLM 自由生成。
- [ ] LLM 只允许润色已有事实，不得新增实验结论。
- [ ] 每个关键结论链接到对应 artifact / evidence。
- [ ] 支持 success / failed / rejected / stopped 四类报告。

### 12.4 Artifact

```text
final_report.md
```

### 12.5 验收标准

- [ ] 任意终态都有报告。
- [ ] 报告中的指标与 `metrics.json` 一致。
- [ ] validity 失败时使用明确警告。
- [ ] 报告可以在无 LLM 模式下生成。

---

## 13. Step 3.10：一键真实纵向 Demo 与回归评测

### 13.1 目标

提供一个命令执行固定案例，并产生完整 artifact 链。

建议 CLI：

```bash
uv run autoad run-mvp \
  --run-id run_patchcore_bottle_001 \
  --case configs/mvp/patchcore_mvtec_bottle.yaml
```

### 13.2 待办

- [ ] 串联 Step 3.0–3.9。
- [ ] 支持从已有 stage 恢复，不重复覆盖已完成 artifact。
- [ ] CLI 显示当前 stage、关键 artifact 和失败原因。
- [ ] 增加固定成功案例 fixture。
- [ ] 增加固定失败案例 fixture。
- [ ] 增加 evaluation leakage 案例。
- [ ] 增加不适合迁移的论文/Idea 案例。
- [ ] 生成一份可供答辩展示的 `runs/demo_*` 脱敏样例。

### 13.3 最终验收

- [ ] 一个固定案例可以从输入运行到报告。
- [ ] 产生真实 baseline 与 variant 指标。
- [ ] 所有关键操作有 event。
- [ ] patch 和命令经过审批。
- [ ] 评价协议未被破坏。
- [ ] 相同版本和配置可重复运行。
- [ ] 成功、失败和拒绝均可生成报告。
- [ ] README 提供 10 分钟内可理解的 Demo 指南。

---

## 14. 测试与评测策略

### 14.1 三层测试

```text
单元测试
  schema、validator、parser、路径和命令边界

集成测试
  artifact → stage → artifact
  使用 fixture，不依赖 GPU 和外部模型

真实案例测试
  固定 PatchCore + MVTec AD 类别
  允许较慢，不在每次普通 CI 中运行
```

### 14.2 建议新增测试标记

```text
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.external_model
@pytest.mark.gpu
@pytest.mark.slow
```

### 14.3 最小 AD-AgentBench

```text
Case 1：固定直接 Idea，完整成功闭环
Case 2：不兼容 Idea，被 Transferability Judge 拒绝
Case 3：patch 尝试修改 evaluation script，被拒绝
Case 4：实验命令失败，仍生成失败报告
Case 5：指标提升但 evaluation fingerprint 变化，判定无效
```

### 14.4 质量指标

```text
schema validation success rate
artifact completeness
source/evidence coverage
command reproducibility
metric parse accuracy
validity violation detection rate
report factual consistency
stage failure observability
```

---

## 15. 近期执行优先级

### P0：立刻执行

```text
Step 3.0 固定案例与 baseline 复现
Step 3.1 真实 Repository Reader
Step 3.2 真实 Paper Reader
Step 3.3 Single Idea 确认
Step 3.4 Transferability Judge
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

### P1：闭环之后

```text
SQLite 元数据仓储
Run / Stage / Artifact / Event 查询
模型调用记录与成本统计
简单 Gradio / Web UI
历史 run 浏览
```

### P2：数据库稳定之后

```text
多 Agent Idea
多个候选去重和选择
Idea → Experiment 关联
低成本多分支 smoke
历史失败经验检索
```

### P3：出现真实需求后

```text
MinIO / S3
PostgreSQL
Temporal / LangGraph durable runtime
多 worker / 多 GPU
多用户与权限
完整可观测性
```

---

## 16. 明确延后条件

### 16.1 数据库

满足以下任意两项再启动：

```text
run 数量超过 100
需要按状态 / baseline / dataset 查询
多个进程并发读写
UI 需要历史分页
需要统计失败率和 stage latency
events.jsonl 查询成为瓶颈
```

### 16.2 对象存储

满足任一项再启动：

```text
本地磁盘成为瓶颈
Runner 与 API 不在同一机器
需要共享 checkpoint / PDF / 图像
需要版本化和生命周期管理
```

### 16.3 Workflow Runtime

满足任意两项再启动：

```text
实验持续数十分钟或数小时
需要进程重启恢复
需要统一 retry / timeout / cancel
需要长时间等待人工审批
需要并行实验分支
需要跨机器 GPU worker
```

### 16.4 多 Agent

以下条件全部满足后再启动：

```text
单一 Idea 真实闭环已经跑通
Idea 与实验结果可以稳定关联
有至少 5 个可评测真实案例
能测量多 Agent 相比单 Agent 的收益
候选去重、证据和用户选择状态有可靠存储
```

---

## 17. 开发节奏建议

每个 Step 采用同一节奏：

```text
1. 定义单一目标和明确非目标
2. 先写 schema / 输入输出契约
3. 写 fixture 与失败用例
4. 实现最小功能
5. 本地相关测试
6. 全量 pytest
7. verify.sh
8. 手工真实案例验证
9. 更新 README / 路线 / notes
10. 单一职责 commit
```

每个 Step 必须回答：

```text
它解决了哪个已经出现的真实问题？
是否真的需要 Agent，还是普通函数即可？
失败时是否留下 artifact 和事件？
用户是否仍拥有关键确认权？
结论是否来自真实材料或实验？
```

---

## 18. 建议的提交序列

```text
chore: lock reproducible PatchCore MVP case
feat: add local repository reader
feat: add real PDF paper reader
feat: add single idea confirmation flow
feat: add transferability judge
feat: add dynamic experiment planning
feat: add patch planning and approval
feat: add controlled experiment runner
feat: add metrics parsing and validity checks
feat: add deterministic final reporting
feat: add end-to-end MVP command
```

每个提交保持可独立测试，不把整个 Step 3.x 压成一个大提交。

---

## 19. 完成定义

当以下条件全部满足时，Step 3.x 真实纵向闭环完成：

- [ ] 使用固定 PatchCore repo 和 commit。
- [ ] 使用固定 MVTec AD 类别。
- [ ] 真实读取一篇论文或接受一个明确用户 Idea。
- [ ] 用户确认唯一 active Idea。
- [ ] 生成有证据的迁移判断。
- [ ] 生成可执行实验计划。
- [ ] 生成并批准最小 patch。
- [ ] 在受控环境运行 baseline 和 variant。
- [ ] 从真实输出解析指标。
- [ ] 检查评价协议和数据泄漏风险。
- [ ] 无论结果如何都生成最终报告。
- [ ] 完整 artifact 和 events 可审计。
- [ ] 固定案例可以重复运行。

完成后再进入 Step 4.x：SQLite 元数据层。

---

## 20. 一句话路线

> **现在先把一个已确认 Idea 在固定 PatchCore + MVTec AD 案例上真正跑完：读材料、判断迁移、生成并批准 patch、执行实验、读取指标、检查有效性、输出报告；闭环跑通后，再引入数据库、多 Agent、对象存储和可靠工作流。**