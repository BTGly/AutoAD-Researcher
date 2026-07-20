# 开发计划 R5：报告 API 与状态接口（对应 PR-R3）

## 1. 目标

在不破坏现有 `/api/runs/{run_id}/report` 的前提下，增加报告版本、状态、内容、证据和下载接口。API 只负责校验、读取和创建持久化 Job，不在请求进程中执行长任务。

## 2. 复用现有代码

- 使用 `RUNS_ROOT`；
- 使用 `run_dir_or_400()` 和现有 run path helper；
- 使用报告 Store、PipelineJob Store 和 Event service；
- 保留当前 `REPORT_PATHS` 兼容读取逻辑；
- 不重新实现 run_id 校验和路径根目录解析。

## 3. API

### 报告管理

```text
GET  /api/runs/{run_id}/reports
GET  /api/runs/{run_id}/reports/latest-created
GET  /api/runs/{run_id}/reports/latest-content-ready
GET  /api/runs/{run_id}/reports/{report_id}/manifest
GET  /api/runs/{run_id}/reports/{report_id}/state
POST /api/runs/{run_id}/reports
POST /api/runs/{run_id}/reports/{report_id}/retry
```

`POST /reports` 只校验 Session/readiness、在锁内同步冻结 Snapshot、分配版本并创建幂等报告 Jobs。重复请求返回既有报告，不重复执行。Snapshot hash 产生前不创建依赖该 hash 的 Job。

Retry 请求必须显式指定允许重试的目标步骤，并调用报告 Job 专用的 failed requeue 操作，沿用该步骤的幂等身份和 retry 记录；不能把一个 PDF 失败无条件扩散成 Facts/Narrative 全链重跑。

`latest-created` 表示最新创建的版本；`latest-content-ready` 表示最新可读且 Validator 通过的版本。两者都是报告侧新增语义，不把不存在的 `/latest` 说成旧兼容接口。旧兼容接口仍只有 `/api/runs/{run_id}/report`，ReportPage 默认使用 `latest-content-ready`，并单独显示更新中的最新创建版本。

`/manifest` 只返回不可变 Manifest；`/state` 返回可变的 `ReportState` 投影。实施前必须结合现有 FastAPI 路由注册和测试确认精确注册位置，不能因为字符串相似而复用错误的路由语义。

### 内容和证据

```text
GET /api/runs/{run_id}/reports/{report_id}/content?format=md
GET /api/runs/{run_id}/reports/{report_id}/content?format=html
GET /api/runs/{run_id}/reports/{report_id}/evidence/{evidence_id}
```

Markdown/HTML 是否可读由对应制品和内容状态决定；不要把不存在的 PDF 作为全部内容接口的统一前置条件。

### 下载

```text
GET /api/runs/{run_id}/reports/{report_id}/download/{artifact}
```

`artifact` 只允许报告侧制品记录中登记且位于固定报告目录的制品。路径参数不能直接拼接成任意文件路径；Manifest 负责身份，State/制品记录负责可下载清单。

### 讨论和审阅（后续阶段注册）

```text
GET  /api/runs/{run_id}/reports/{report_id}/discussion
POST /api/runs/{run_id}/reports/{report_id}/discussion
POST /api/runs/{run_id}/reports/{report_id}/proposals
POST /api/runs/{run_id}/reports/{report_id}/review-decision
```

## 4. 共享校验

```text
require_run(run_id)
→ require_manifest(report_id)
→ verify_report_dir_is_under_run()
→ verify_manifest_identity()
→ verify_artifact_allowlist()
→ verify_artifact_exists_and_sha()
```

必须测试：

- `../`、绝对路径和空字节；
- symlink 逃逸；
- `/runs/foo` 与 `/runs/foobar` 前缀碰撞；
- 不在 manifest 中的 artifact；
- 超大文件和错误 MIME；
- 非法 `report_id`、`run_id` 和版本。

下载响应使用真实 MIME 和 `Content-Disposition`，不能一律返回文本或依赖文件扩展名猜测。

现有 `ArtifactReferenceV2` 不包含 MIME、下载文件名或 Content-Disposition。报告侧必须保存独立的制品交付记录；它引用 `ArtifactReferenceV2`，但不修改该通用 schema，也不从扩展名猜测真实 MIME。

## 5. 状态返回

Manifest/API 状态必须同时返回：

```text
generation_status
review_status
format_status
jobs
retry_count
last_error
available_artifacts
```

`READY_FOR_REVIEW` 可以作为前端兼容字段，但它必须是确定性 readiness projection，而不是 PDF 是否成功的别名。

## 6. 旧接口兼容

现有接口继续工作：

```text
GET /api/runs/{run_id}/report
```

兼容顺序：

1. 存在可读的新报告时返回指定版本内容和 `report_id`；
2. 否则按当前代码中真实的 `REPORT_PATHS` 查找旧制品；
3. 没有报告时返回当前兼容语义。

旧接口只读，不创建新版 Manifest，不覆盖旧报告。

## 7. 验收

- [ ] 相同报告请求只创建一组幂等 Jobs。
- [ ] Retry 只重排队指定目标步骤，并保留失败原因和 retry 次数。
- [ ] failed Job 的重排队通过显式报告 Job 操作完成，不改变普通幂等创建的读取语义。
- [ ] `latest-created` 与 `latest-content-ready` 语义不混淆，排队中的新版本不会遮挡旧的可读版本。
- [ ] `/manifest` 只返回不可变身份；`/state` 返回可变状态投影。
- [ ] API 使用 `RUNS_ROOT` 和现有 path helper。
- [ ] `report_id` 固定绑定，不能在 Agent 请求中隐式切换 latest。
- [ ] Markdown/HTML 在存在且验证通过时可读取，即使 PDF 失败。
- [ ] 不存在的制品返回明确的 404/409，而不是目录穿越结果。
- [ ] 下载 MIME、文件名和版本正确。
- [ ] 报告制品交付记录明确保存 MIME、下载文件名和 Content-Disposition。
- [ ] 旧 `/report` 路由仍能读取旧报告。
- [ ] 路径攻击、symlink 和前缀碰撞测试通过。

## 8. 不做什么

- 不新增认证系统。
- 不在 API 请求中直接调用 LLM、TeX 或长任务。
- 不提供修改/删除已冻结报告的接口。
- 不把所有下载都强制绑定 PDF 或 ZIP READY。
- 不让 API 层自行转换 Markdown；转换由 renderer Job 完成。
