# 开发计划 R1：报告 Schema 与生命周期（对应 PR-R0）

## 1. 目标

先定义报告身份、快照、制品和状态边界，并把报告生成接入现有 `PipelineJobStore`、Worker 和 EventStore。此阶段不调 LLM，不渲染 PDF，不改前端。

## 2. 设计依据

| 来源 | 可复用机制 | 处理 |
|---|---|---|
| AutoAD `ExperimentSessionStore` | 锁内身份校验、原子写、revision | 直接复用存储模式 `[REFER]` |
| AutoAD `PipelineJobStore` | `create_or_get_pipeline_job()`、claim/complete/fail | 报告 Job 直接接入 `[REFER]` |
| Arbor | 缺失输入仍可生成 partial report | 采用其容错思想 `[REFER]` |
| ARIS `run_state.py` | 原子 replace、单 run lock、执行完成与验收分离 | 重实现到报告状态 `[REIMPL]` |
| Claw-AI-Lab stage contract | 输入、输出、重试和错误码显式化 | 参考，不复制整套 pipeline `[REFER]` |

## 3. 报告对象边界

### 3.1 `ReportManifest`（计划新增）

Manifest 只描述一个报告版本的身份、制品和可变状态。建议字段如下；最终字段名在实现前以 AutoAD 现有 schema 风格核对：

```text
schema_version
run_id
session_id
report_id
version
source_snapshot_content_sha256
facts_content_sha256
created_at
updated_at
generation_status
review_status
format_status
artifact_refs
previous_report_id
parent_report_id
```

`artifact_refs` 使用现有 `ArtifactReferenceV2`，不新造不带 SHA 的裸字典。报告字段中同时保存：

- `content_sha256`：对去除时间戳等 volatile 字段后的 canonical JSON 计算，用于身份和幂等；
- `artifact_sha256`：对实际文件字节计算，用于下载校验。

### 3.2 `ReportSnapshot`（计划新增）

Snapshot 是冻结的来源清单，不复制所有实验数据：

```text
run_id
session_id
source_refs: list[ArtifactReferenceV2]
session_revision
evaluation_contract_ref
environment_snapshot_ref
source_inventory_sha256
frozen_at
```

所有 `source_refs` 必须是 run-relative、类型明确、存在且 SHA 匹配的 artifact。解析路径必须拒绝绝对路径、`..`、symlink 逃逸和前缀碰撞。

## 4. 三类状态

### 4.1 生成状态

```text
queued
building_snapshot
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

### 4.3 审阅状态

```text
unreviewed / accepted / needs_more / needs_repair / disputed
```

报告生成完成不代表用户接受科学结论。`ARCHIVED` 不用作“已接受”“旧版本”和“停止展示”的混合状态；保留策略以后单独建模。

## 5. 报告 Job 主链

报告任务必须进入现有持久化队列，不得在 API 中使用 `asyncio.create_task()`。

```text
report_snapshot_build
→ report_facts_assemble
→ report_narrative_generate
→ report_validate
→ report_render_html
→ report_package
→ report_render_pdf（可选）
```

每一步使用现有 `create_or_get_pipeline_job()`，幂等身份采用：

```text
report:{session_id}:{snapshot_content_sha256}:{step}
```

实现时必须同步修改 Worker 的 job dispatch、事件、失败和恢复路径；不能只在计划里新增 Job 名称而不定义 Worker 处理。

## 6. 状态转换规则

状态转换由代码决定，不由 LLM 判断。建议转换关系：

```text
queued
  → building_snapshot
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

## 7. Store 要求

建议新增 `ReportStore`，但不再另建数据库：

```text
runs/{run_id}/reports/{report_id}/
├── report_manifest.json
└── report_state.json
```

创建和版本分配必须在同一把锁内完成：

```text
不存在 → 原子创建
已存在且身份一致 → 返回磁盘中的权威对象
已存在但身份不同 → 抛出冲突
```

所有 JSON 使用临时文件、flush/fsync、`os.replace()`；失败不得留下可被误读的半截 JSON。状态更新保留 revision 并写事件。

## 8. 验收

- [ ] 并发两次相同请求只生成一个 report version。
- [ ] 同幂等键、相同身份返回已存在对象。
- [ ] 同幂等键、不同身份报告冲突。
- [ ] Snapshot 引用只允许已登记、SHA 匹配的 artifact。
- [ ] 非法状态转换被拒绝。
- [ ] Markdown/Validator 完成后，即使 PDF 不可用，也能进入可审阅条件。
- [ ] 失败重试通过持久化 Job，而不是进程内后台任务。
- [ ] 旧报告目录和文件字节不发生改变。

## 9. 不做什么

- 不添加 `FINALIZED`、`COMPLETED` 等当前 `SessionStatus` 中不存在的状态。
- 不添加新的 `IterationStore`。
- 不创建报告专用数据库、后台执行器或第二套事件总线。
- 不在此阶段调用 LLM、渲染 PDF 或实现前端。
