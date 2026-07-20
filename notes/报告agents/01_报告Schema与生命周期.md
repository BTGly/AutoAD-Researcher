# 开发计划 R1：报告 Schema 与生命周期（对应 PR-R0A / PR-R0B）

## 1. 目标

先定义权威来源适配、可复现 Snapshot、报告身份、制品和状态边界，并把报告生成接入现有 `PipelineJobStore`、Worker 和 EventStore。Snapshot 只冻结小型可变控制面对象；此阶段不调 LLM，不渲染 PDF，不改前端。

## 2. 设计依据

| 来源 | 可复用机制 | 处理 |
|---|---|---|
| AutoAD `ExperimentSessionStore` | 锁内身份校验、原子写、revision | 直接复用存储模式 `[REFER]` |
| AutoAD `PipelineJobStore` | `create_or_get_pipeline_job()`、claim/complete/fail | 报告 Job 直接接入 `[REFER]` |
| AutoAD `CognitiveBudgetStore` | append-only usage ledger、账本锁和可重建 cost summary | 在同一账本锁读取记录、生成冻结摘要并校验 fingerprint `[REFER]` |
| Arbor | 缺失输入仍可生成 partial report | 采用其容错思想 `[REFER]` |
| ARIS `run_state.py` | 原子 replace、单 run lock、执行完成与验收分离 | 重实现到报告状态 `[REIMPL]` |
| Claw-AI-Lab stage contract | 输入、输出、重试和错误码显式化 | 参考，不复制整套 pipeline `[REFER]` |

R0A 的 Snapshot 构造是短时的来源读取和 canonical hash 计算，不为它增加异步排队层。认知成本读取复用 `CognitiveBudgetStore` 的账本锁，不新增跨 Store 全局锁。Arbor 的报告生成允许 partial 输入，ARIS 的原子状态和单运行锁用于恢复边界；两者都不要求复制整个运行目录。

## 3. 报告对象边界

### 3.1 `ReportManifest`（计划新增，不可变）

Manifest 只描述一个报告版本的不可变身份和 lineage，不保存生成/审阅/格式状态。建议字段如下；最终字段名在实现前以 AutoAD 现有 schema 风格核对：

```text
schema_version
run_id
session_id
report_id
version
source_snapshot_content_sha256
created_at
previous_report_id
parent_report_id
parent_session_ref
snapshot_policy_hash
report_recipe_hash
```

`report_recipe_hash` 是报告生成配方的 canonical hash，至少覆盖 Facts schema 版本、Narrative prompt/template 版本、model/profile、Validator 版本和 Renderer 版本。相同 `snapshot_content_sha256 + report_recipe_hash` 的请求复用已有报告；Snapshot 相同但配方变化时创建新的报告版本，不把配方变化伪装成同一报告的重试。

`report_manifest.json` 创建后不覆盖。Facts、Evidence 和制品仍使用现有 `ArtifactReferenceV2`；报告下载所需的 MIME、文件名和 Content-Disposition 另由报告侧制品记录保存，不修改现有 schema。报告字段中同时保存：

- `content_sha256`：对去除时间戳等 volatile 字段后的 canonical JSON 计算，用于身份和幂等；
- `artifact_sha256`：对实际文件字节计算，用于下载校验。

### 3.2 `ReportState`（计划新增，可变且 revisioned）

`report_state.json` 是唯一的可变状态投影，建议包含：

```text
state_revision
generation_status
review_status
format_status
jobs
retry_count
last_error
available_artifacts
updated_at
```

状态和制品条目更新使用同一份原子 JSON。已生成文件字节不可覆盖；`available_artifacts` 只追加新制品或更新其状态，不承担报告身份事实。

`jobs` 中每个报告 Job 都带自己的依赖投影，不使用一个覆盖整条 DAG 的全局字符串：

```text
job_id
job_type
status
depends_on_job_id: string or explicit missing marker
dependency_status: ready / pending / blocked_by_failed_dependency
dependency_reason: string or explicit missing marker
```

`dependency_status` 是当前依赖检查的确定性投影，不改变 `PipelineJob` 的基础状态语义；没有依赖的 Job 使用 `ready`，依赖尚未完成使用 `pending`，上游失败但允许报告专用重试时使用 `blocked_by_failed_dependency`。

### 3.3 `ReportSnapshot`（计划新增，冻结输入）

Snapshot 不是“路径清单”，而是可复现的输入包：小型且会原地变化的控制面对象保存 canonical 冻结副本，大型或本身不可变的制品保存 `ArtifactReferenceV2`。

```text
run_id
session_id
session_revision
frozen_session: canonical object
frozen_idea_tree: canonical object
frozen_champion_pointer: canonical object
frozen_stop_decision: canonical object or explicit missing marker
frozen_attempts: list[AttemptSnapshotV1]
frozen_cognitive_cost_summary: canonical object or explicit missing marker
cognitive_usage_sha256: canonical usage-ledger fingerprint or explicit missing marker
source_refs: list[ArtifactReferenceV2]
evaluation_contract_ref
environment_snapshot_ref
source_inventory_sha256
frozen_at
```

不把当前不存在的 Review 对象伪装成 Snapshot 来源；报告自身的 `review_status` 属于 `ReportState`。所有大型 `source_refs` 必须是 run-relative、类型明确、存在且 SHA 匹配的 artifact。解析路径必须拒绝绝对路径、`..`、symlink 逃逸和前缀碰撞。

`ExperimentAttempt` 是会持续更新的运行时控制面对象，不能只在 Snapshot 中保存 live Attempt JSON 的引用。`AttemptSnapshotV1` 只保留报告所需的小型投影：

```text
attempt_id
session_id
attempt_purpose
job_type
runtime_status_at_snapshot
revision
retry_of
retry_count
pipeline_job_id
failure_code
execution_result_ref
execution_result_binding_status: bound / missing / ambiguous / sha_mismatch
execution_result_missing_reason: string or explicit missing marker
evaluation_contract_ref
evaluation_contract_sha256
created_at
updated_at_at_snapshot
```

`execution_result_ref` 继续保留现有 Attempt 字段语义，但在 Snapshot 中必须解析到 `source_refs` 里唯一的 `ArtifactReferenceV2`：其 `artifact_id`、类型、run-relative locator 和 SHA 必须一致。绑定成功时写 `bound`；引用为空或缺失写 `missing`，引用不唯一写 `ambiguous`，SHA 校验失败写 `sha_mismatch`，并填写 `execution_result_missing_reason`。不另造第二套执行结果引用模型；Facts/Evidence Validator 只消费这个结构化绑定结果。

PID、process group、resource lease 和 heartbeat 不进入报告身份；若以后展示，必须明确是 Snapshot 时刻的运行状态。`CognitiveCostSummary` 是从 append-only usage ledger 重建的派生视图，R0A 冻结其结果，R1 不再调用 live builder。

## 4. 三类状态

### 4.1 生成状态

```text
queued
assembling_facts
generating_narrative
validating
content_ready
failed
```

### 4.2 格式状态

Markdown、HTML、PDF 分别记录：

```text
missing / queued / ready / failed
```

PDF 不属于内容正确性的前置条件。

Snapshot 构造是请求阶段的锁内短操作，不作为持久化生成状态；持久化 Job 从 `assembling_facts` 开始。

### 4.3 审阅状态

```text
unreviewed / accepted / needs_more / needs_repair / disputed
```

报告生成完成不代表用户接受科学结论。`ARCHIVED` 不用作“已接受”“旧版本”和“停止展示”的混合状态；保留策略以后单独建模。

## 5. PR-R0A：同步冻结 Snapshot

`POST /reports` 在现有 run/report 锁内完成版本分配和 Snapshot 写入，但不依赖一把跨 Store 的全局锁。各来源 Store 已有自己的锁，因此来源一致性用短时 optimistic double-read 保证：

```text
校验 Session/readiness
→ 读取各来源的稳定 revision；没有 revision 时计算 canonical content hash
→ 读取 Attempt inventory，复制小型控制面对象并 canonicalize，校验大型 artifact refs
→ 在 `CognitiveBudgetStore` 现有账本锁内：记录 ledger fingerprint_before，读取同一批 usage records，基于这批 records 生成 CognitiveCostSummary，再记录 fingerprint_after
→ 只有 fingerprint_before == fingerprint_after 才接受该 summary，并将同一 fingerprint 保存为 `cognitive_usage_sha256`
→ 再次读取其他来源 revision/hash 和 Attempt inventory/revision
→ 全部未变化：计算 source inventory 和 snapshot_content_sha256，原子写入 Snapshot
→ 任一来源变化：丢弃本轮副本，在有限次数内重试；仍不稳定则返回冲突/稍后重试
```

认知账本的“读取记录、生成摘要、前后 fingerprint 校验”必须属于同一个账本读取窗口；不能先读取旧摘要、再用后来的 fingerprint 装饰它。实现时可新增一个返回 records+fingerprint 的小型 Store 操作，具体函数名以实际代码核对为准，不增加全局锁或独立账本数据库。

报告锁只保护 report version 分配和 Snapshot 写入，不保护 Session、IdeaTree 等来源，也不在多个 Store 之间取得锁；这样避免锁顺序和死锁问题。重复请求返回已有 Snapshot；同一请求身份但来源 revision 或内容不一致时，只有双读稳定后才允许创建报告。这里不创建 `report_snapshot_build` Job。

## 6. PR-R0B：报告 Job 主链

报告任务必须进入现有持久化队列，不得在 API 中使用 `asyncio.create_task()`。

```text
report_facts_assemble
→ report_narrative_generate
→ report_validate
   ├── report_render_html
   │   └── report_package
   └── report_render_pdf（可选）
```

这是固定的报告 DAG，不新增通用调度器：`report_package` 依赖 HTML ready，PDF 只依赖 Validator。各格式失败不会回退 `content_ready`，也不会阻断另一条已满足依赖的分支。

报告 Job 的依赖失败只做报告侧的逐 Job 依赖投影：下游 `PipelineJob` 保持 `queued`，对应 `ReportState.jobs` 条目记录 `dependency_status: blocked_by_failed_dependency`、具体的 `depends_on_job_id` 和原因，Worker 不把它 claim 后永久置为 `failed`。上游 Job 通过显式报告重试成功后，下一轮依赖检查只清除对应 Job 的阻塞投影，下游自然具备执行条件；PDF 分支的状态不被 HTML 分支的阻塞覆盖。非报告 Job 继续使用当前 Worker 的严格失败传播，不把这条例外推广成通用调度规则。

报告 Job 失败重试必须使用独立的、受限的 failed requeue 操作，而不是让 `create_or_get_pipeline_job()` 隐式改变状态。该操作只允许报告 Job 类型，并在同一把 Job 锁内：

```text
failed
→ queued
→ retry_count + 1
→ 清理 started_at / completed_at / error
→ 保留 job_id、payload、idempotency_key
→ 写 retry event
```

普通幂等读取仍只返回已有 Job；retry API 必须显式指定目标步骤。

每一步使用现有 `create_or_get_pipeline_job()`，Snapshot 已经存在后，幂等身份采用：

```text
report:{report_id}:{snapshot_content_sha256}:{step}
```

实现时必须同步修改 Worker 的 job dispatch、事件、失败和恢复路径；不能只在计划里新增 Job 名称而不定义 Worker 处理。

报告 Job 必须有明确的恢复租约：短步骤可以使用现有 300 秒恢复边界；可能超过该边界的 Narrative/PDF handler 必须在租约内 heartbeat，或使用报告专用的可观察 lease 配置。PDF 仍受编译器自身 timeout 约束。不能让普通 requeue 在没有 owner/heartbeat 证据时重复执行同一报告 Job。

## 7. 状态转换规则

状态转换由代码决定，不由 LLM 判断。建议转换关系：

```text
queued
  → assembling_facts
  → generating_narrative
  → validating
  → content_ready

任一生成步骤 → failed
failed → 对应步骤重新排队（保留失败事件和 retry 次数）
```

`review_status` 和格式状态独立更新。可审阅条件由确定性 readiness service 计算：

```text
Snapshot 已冻结
+ Facts 可解析
+ Markdown 存在
+ Validator 通过
```

不要求 PDF 或 ZIP 成功。

## 8. Store 要求

建议新增 `ReportStore`，但不再另建数据库：

```text
runs/{run_id}/reports/{report_id}/
├── report_manifest.json
├── report_state.json
└── snapshot.json
```

创建和版本分配必须在同一把锁内完成：

```text
不存在 → 原子创建
已存在且身份一致 → 返回磁盘中的权威对象
已存在但身份不同 → 抛出冲突
```

所有 JSON 使用临时文件、flush/fsync、`os.replace()`；失败不得留下可被误读的半截 JSON。状态更新保留 revision 并写事件。

## 9. 验收

- [ ] 并发两次相同请求只生成一个 report version。
- [ ] 同幂等键、相同身份返回已存在对象。
- [ ] 同幂等键、不同身份报告冲突。
- [ ] 创建报告后修改 Session，旧 Snapshot 的 canonical 内容和 hash 不变。
- [ ] 创建报告后修改 IdeaTree 或 Champion pointer，旧 Snapshot 仍可重建。
- [ ] Snapshot 构造期间并发修改任一来源时，不会写出混合 revision 的 Snapshot；有限重试耗尽后会明确失败。
- [ ] Attempt 在报告创建后继续 heartbeat 或终止时，旧 Snapshot 的 `frozen_attempts` 不变，且不依赖 live Attempt JSON 的 SHA。
- [ ] CognitiveCostSummary 在报告创建后重建时，旧 Snapshot 仍使用冻结结果。
- [ ] Snapshot 的 `frozen_cognitive_cost_summary` 与 `cognitive_usage_sha256` 来自同一账本读取窗口；账本在窗口内变化时不会写出混合摘要和指纹。
- [ ] Snapshot 引用只允许已登记、SHA 匹配的 artifact。
- [ ] `execution_result_ref` 能唯一解析到 `source_refs` 中的 `ArtifactReferenceV2`；无法解析时保留显式 missing marker。
- [ ] `execution_result_binding_status` 能区分 bound、missing、ambiguous 和 sha_mismatch，并保留可解释的缺失原因。
- [ ] 相同 Snapshot 和相同 `report_recipe_hash` 复用报告；配方变化生成新版本。
- [ ] Snapshot hash 产生前不会创建依赖该 hash 的 Job。
- [ ] 非法状态转换被拒绝。
- [ ] 报告 Job 超过普通 300 秒恢复边界时不会被重复 claim。
- [ ] failed 报告 Job 只能通过显式受限 requeue 重试；普通 `create_or_get_pipeline_job()` 不会暗中重新执行。
- [ ] PDF 不依赖 Bundle；HTML 失败不会错误地让 PDF 进入依赖失败，Bundle 只在 HTML ready 后生成。
- [ ] 报告下游依赖失败时保持 queued 和阻塞投影；上游报告 Job 成功重试后，下游可自然恢复执行。
- [ ] `/state` 能逐 Job 返回依赖状态、上游 Job ID 和原因；HTML 阻塞不会覆盖 PDF 分支状态。
- [ ] Markdown/Validator 完成后，即使 PDF 不可用，也能进入可审阅条件。
- [ ] 失败重试通过持久化 Job，而不是进程内后台任务。
- [ ] 旧报告目录和文件字节不发生改变。

## 10. 不做什么

- 不添加 `FINALIZED`、`COMPLETED` 等当前 `SessionStatus` 中不存在的状态。
- 不添加新的 `IterationStore`。
- 不创建报告专用数据库、后台执行器或第二套事件总线。
- 不在此阶段调用 LLM、渲染 PDF 或实现前端。
