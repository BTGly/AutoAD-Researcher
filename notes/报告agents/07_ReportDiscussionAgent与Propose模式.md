# 开发计划 R7：Report Discussion Agent 与 Propose 模式（对应 PR-R4/R5）

## 1. 目标

提供与主 Chat 隔离的报告讨论上下文：只能解释、核查和比较已冻结报告，不能修改文件、执行命令、创建 Job、修改 Session 或直接执行后续实验。

## 2. 参考依据

| 来源 | 已核对的事实 | 本项目处理 |
|---|---|---|
| Arbor Companion | 独立 provider、独立上下文、只读工具、不污染主运行 | `[REFER]` / `[REIMPL]` |
| DeepAgents 0.6.10 | 默认注入文件工具、`execute`、`task`；`tools=[]` 不会移除它们 | 必须在测试中验证工具注册表 |
| DeepAgents 0.6.10 | `FilesystemPermission` 作用于内置文件工具；`HarnessProfile.excluded_tools` 可排除工具 | 按锁定版本核对后使用，不能引用旧内部路径 |
| AutoAD V2 | Decision→Gate→Reply、结构化输出 | 复用边界，不复用主 Chat transcript |

## 3. 首版安全模型

```text
无通用 FilesystemBackend
+ 不注册 execute / shell / task
+ 只注册窄接口 typed tools
+ 每个工具校验 report_id + snapshot_hash
+ 每个工具校验 artifact 类型、相对路径、文件大小和行数上限
+ transcript 由应用层持久化
```

如果实际 DeepAgents 版本无法可靠移除默认工具，首版不把它暴露在 Web API；改用已有 LLM structured call 加应用层 typed tool dispatcher。不得把 prompt 中的“只读”当作安全边界。

## 4. 对话持久化

首版使用：

```text
runs/{run_id}/reports/{report_id}/discussion/messages.jsonl
```

Transcript 仍可使用 JSONL，但幂等和恢复的逻辑单位是一个 `DiscussionTurn`，而不是孤立的 user/assistant 两条消息。每个 turn 保存：

```text
turn_id
request_id
report_id
snapshot_content_sha256
user_message
response
status: pending / completed / failed
owner_id（计划新增，处理者身份）
lease_expires_at（计划新增，处理租约到期时间）
evidence_ids
created_at
completed_at
error
```

写入复用 V2 event service 的锁内 JSONL 追加模式：`request_id` 由客户端或 API gateway 提供，并在网络重试时复用；服务层在锁内校验/占用它，再分配 `turn_id`。先持久化 pending turn，再由首个成功获得 owner/lease 的处理者继续完成，append 后 flush/fsync。相同 `request_id` 若已有 completed/failed turn，返回该 turn；若已有 pending turn 且租约仍有效，其他请求明确返回 pending；只有原 owner 或在锁内成功接管已过期租约者可以继续写入。进程崩溃后不得把“只有 user 消息、没有 assistant 回复”当作已完成请求。

owner/lease 只是 DiscussionTurn 的最小单写者保护，不扩展成通用任务锁或新的 checkpoint 系统；租约时长和续租方式实施时复用当前 Job 的恢复边界并以实际配置核对。

同一个 `turn_id` 的 pending 和 completed/failed 记录由读取器按最后状态折叠；如果实现选择单文件原子更新，也必须保留相同的状态和恢复语义，不需要另建复杂的 checkpoint 系统。

读取时只允许忽略最后一条未完成的物理尾行并记录 transcript warning；中间损坏行不能静默当作正常消息。Transcript 设置最近消息窗口和总字节上限，超过上限时保留最近窗口并写入明确的 truncation marker，不为首版增加独立归档系统。

Transcript 是用户对话记录，不是 Facts 的权威来源。上下文装配时只加载有上限的最近消息，并把 report snapshot identity 固定注入。

暂不使用计划中未核实的 `storage/checkpoint/sqlite.py` 路径，也不把 LangGraph checkpoint 当作业务 transcript 的唯一事实源。

## 5. Report Context

启动上下文只包含：

```text
report_id
snapshot_content_sha256
report_digest
事实状态摘要
Attempt 摘要
Champion 摘要
停止事实
不确定性
```

深查通过 typed tools：

```text
get_report_digest
get_report_section
list_attempts
get_outcome_card
get_scientific_assessment
get_metrics
get_patch_diff（限制行数）
search_log（限制结果数和文件类型）
read_log_range（限制行数）
get_evaluation_contract
get_environment_snapshot
get_champion
get_budget_usage
resolve_evidence
```

工具只返回应用层摘要或经过 allow-list 的内容，不允许任意路径浏览。

Artifact、日志和 Evidence 内容一律视为不可信数据；工具返回时使用明确的数据边界，内容中的指令不能改变系统提示、工具权限或报告身份。

## 6. Discussion 预算与结构化响应

每个 Report Discussion 需要一个可配置的 `ReportDiscussionBudget`，至少限制：

```text
max_llm_calls
max_output_tokens
max_wall_time_seconds
max_concurrent_requests
max_retries
```

这些是服务层安全上限，不用关键词判断用户意图。LLM 失败只允许有限重试；超出预算返回可解释的 budget/error 状态，不继续偷偷调用。

Discussion 响应使用结构化对象：

```text
answer
response_kind
evidence_ids
unsupported_claims
proposal_candidate
```

`EXPLAIN`、`VERIFY`、`COMPARE` 中的重要事实必须引用有效 `evidence_ids`；如果 Facts/Evidence 无法支持，返回证据不足和 `unsupported_claims`，不把“尽量附证据”当作通过条件。

## 7. Discuss / Propose

### Discuss

允许：

- 解释指标、状态和科学结论；
- 比较 Attempt；
- 核查 Evidence；
- 讨论不确定性和是否需要更多实验。

返回普通解释；其中涉及报告事实的解释必须附有效 `evidence_ids`，纯方法背景或对话组织文字可以不附。

### Propose

用户主动切换后，服务层要求结构化 `FollowUpProposal`。Agent 只能填写候选建议；Proposal 的验证、持久化和 handoff 由应用代码完成。

Agent 不得：

- 创建 PipelineJob、Attempt 或 Session；
- 修改代码、报告、OutcomeCard 或合同；
- 将“接受结论”伪装成实验 Proposal；
- 把未登记的 Evidence 当作依据。

## 8. Intent

保持少量稳定意图：

```text
EXPLAIN
VERIFY
COMPARE
REQUEST_EVIDENCE
DISCUSS_NEXT_STEP
PROPOSE_CONFIRMATION
RETRY_FAILED
REFINE_CURRENT
PIVOT
```

意图分类只是路由提示，不能替代 ProposalService 和 ReviewService 的确定性校验。

## 9. 验收

- [ ] 工具注册表中不存在 execute、shell 和任意文件写入工具。
- [ ] `tools=[]` 不被当作删除默认工具的证明。
- [ ] 绝对路径、`..`、symlink、未登记 artifact 和超大日志均被拒绝或截断。
- [ ] 讨论固定绑定 `report_id` 和 snapshot hash。
- [ ] 多轮 transcript 可恢复但不污染主 Chat。
- [ ] user turn 已持久化但 assistant 尚未完成时，重放同一 `request_id` 不会重复追加 user message，并能继续、失败或明确返回 pending。
- [ ] pending turn 只有原 owner 或成功接管已过期 lease 的处理者能继续写入；并发重复请求不会生成第二个回复。
- [ ] 并发 append、客户端重复 request_id、尾部半行和中间损坏行按契约处理。
- [ ] 预算、超时、并发和有限重试生效。
- [ ] EXPLAIN/VERIFY/COMPARE 在缺少证据时明确返回证据不足。
- [ ] Discuss 返回解释，不返回可直接执行的 Job。
- [ ] Propose 返回结构化 Proposal，且未确认前不会创建 Job。
- [ ] Agent 无法直接修改任何报告或实验制品。

## 10. 不做什么

- 不使用通用 `FilesystemBackend` 读取整个 run 目录。
- 不依赖旧版 DeepAgents 内部 SQLite import 路径。
- 不让 Agent 直接写 Proposal 文件、创建 Job 或执行实验。
- 不把完整日志塞进 system prompt。
- 不首版实现重型多 Agent 分析编排。
