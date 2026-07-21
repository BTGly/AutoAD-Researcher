# AutoAD 报告 Agents 设计与开发计划文档索引（修订版）

> 工作稿位置：`/root/autodl-tmp/AI4S/notes/报告agents/`
>
> `参考/报告agents/` 保留为原始参考稿，本目录是根据代码对照审阅和成熟项目核对后的实施计划。

## 一、范围

覆盖：实验报告事实冻结、报告生成、Markdown/HTML/PDF 制品、下载、证据定位、只读讨论、用户确认后的后续实验转交和审阅记录。

不覆盖：研究意图对齐、仓库分析、材料解析、中间实验自迭代平台，以及新的通用任务调度框架。

## 二、外部参考复用等级

| 标签 | 含义 | 本项目处理方式 |
|---|---|---|
| `[COPY]` | 直接纳入代码 | 仅限许可证允许且经过逐文件审查的代码；保留 LICENSE/NOTICE |
| `[ADAPT]` | 保留局部机制并适配 | 固定来源仓库和提交，记录本地修改和许可证 |
| `[REIMPL]` | 按已观察行为独立实现 | 不复制源码，补充本项目测试 |
| `[REFER]` | 只参考架构、提示词或交互思想 | 不形成运行时依赖 |

每条复用记录必须能回答：来源仓库、实际文件路径、许可证、复制还是重写、AutoAD 的本地差异、是否需要 NOTICE。没有这些信息时只能标 `[REFER]`。

## 三、已核对的成熟项目

| 项目 | 实际路径 | 可吸收的机制 | 本计划中的处理 |
|---|---|---|---|
| Arbor | `/root/autodl-tmp/repos/Arbor/` | partial `REPORT.md`、确定性拼接、自包含 HTML、artifact export、独立只读 Companion | `[REFER]`；HTML/Companion 可按需 `[ADAPT]`，保留 Apache-2.0 要求 |
| Claw-AI-Lab | `/root/autodl-tmp/repos/Claw-AI-Lab/` | 阶段输入/输出契约、artifact manifest、编译预检、timeout、非阻塞 LaTeX、PIVOT/REFINE 版本化 | `[REFER]` / 局部 `[ADAPT]`；不复制其论文流水线 |
| AI-Scientist | `/root/autodl-tmp/repos/AI-Scientist/` | 逐节写作、结果不得臆造、编译和引用检查 | `[REFER]`；其源码使用专用 Source Code License，不复制 |
| AiScientist | `/root/autodl-tmp/repos/AiScientist/` | MIT 许可下的科研工作流和报告组织思路 | `[REFER]`，若需代码另行做许可证审查 |
| DeepAgents | `/root/autodl-tmp/repos/deepagents/` | `response_format`、filesystem permission、`HarnessProfile.excluded_tools`、持久化图状态 API | `[REFER]`；按 AutoAD 锁定版本核对 API，不按旧路径硬写 |
| ARIS | `references/research-automation/Auto-claude-code-research-in-sleep/` | 原子 JSON、单运行锁、可恢复队列、`done` 与 `accepted` 分离 | `[REFER]` / `[REIMPL]` |
| AutoSOTA | `/root/autodl-tmp/repos/AutoSOTA/` | inspect/report CLI 和多格式查看思路 | `[REFER]` |

`references/research-automation/Arbor`、`Claw-AI-Lab`、`AiScientist` 是指向 `/root/autodl-tmp/repos/` 的断链；实施文档统一使用上表的真实路径，不引用断链路径。

## 四、AutoAD 现有能力（直接接入，不重新设计）

| 组件 | 实际位置 | 报告侧用途 |
|---|---|---|
| `ExperimentSession` / `ExperimentSessionStore` | `projects/AutoAD-Researcher/src/autoad_researcher/experiment/` | Session 身份、状态、合同和原子存储 |
| `PipelineJobStore` 服务函数 | `assistant/v2/job_service.py` | 报告 Job 的幂等、claim、complete、fail |
| `EventStore` / V2 event service | `core/events.py`、`assistant/v2/event_service.py` | 阶段事件和前端状态 |
| `ExperimentAttemptService` | `experiment/attempt_service.py` | confirmatory/retry/refine 的 Attempt/Job 入口 |
| `OutcomeCard` / `ScientificValidityReport` | `experiment/finalizer.py`、`supervisor/validity.py` | 权威执行和科学事实 |
| `ArtifactReferenceV2` | `schemas/artifacts.py` | 带 SHA 的类型化 artifact 引用 |
| `CandidateRegistry` / `ChampionPointer` | `experiment/promotion.py` | Champion 和候选指针 |
| `CognitiveCostSummary` | `experiment/cost_summary.py` | 认知成本和资源摘要 |
| `StopDecision` | `experiment/stop_policy.py` | 停止事实 |
| `TaskBridge` | `assistant/v2/task_bridge.py` | PIVOT 的待确认任务入口，但受 `input_task.yaml` 单次物化约束 |
| `ReportPage` / `report_route` | `frontend/src/components/ReportPage.tsx`、`server/routes/report_route.py` | 现有兼容报告入口，原地升级 |

旧的 `ReportFacts` 只保留兼容读取，不直接扩展为新报告事实契约。

## 五、文档与实施顺序

| 文档 | 主题 | 对应实施阶段 |
|---|---|---|
| `01_报告Schema与生命周期.md` | 报告身份、状态、Manifest、Job 契约 | PR-R0 |
| `02_Facts与Evidence索引.md` | Snapshot、Facts、Evidence、Digest | PR-R1 |
| `03_报告正文与Validator.md` | Narrative、Validator、Markdown | PR-R2 |
| `04_PDF_HTML_渲染与打包.md` | HTML 优先，PDF/Bundle 后置 | PR-R3 / PR-R6 |
| `05_报告API与状态接口.md` | API、下载和兼容路由 | PR-R3 |
| `06_ReportPage交互式前端.md` | 最小报告工作区 | PR-R3 |
| `07_ReportDiscussionAgent与Propose模式.md` | 只读 Discussion 和 Propose | PR-R4 / PR-R5 |
| `08_转交_版本与审阅.md` | Review、Proposal、Handoff、lineage | PR-R5 |
| `开发大纲.md` | 总体架构、测试和验收 | 全阶段 |

最终顺序：

```text
PR-R0 控制面接入
→ PR-R1 Snapshot + Facts + Evidence
→ PR-R2 Narrative + Validator + Markdown
→ PR-R3 API + 最小前端 + HTML
→ PR-R4 Read-only Discussion
→ PR-R5 Proposal + Review + Handoff
→ PR-R6 可选 PDF + Bundle
```

## 六、当前实现状态（2026-07-21）

- 已完成：Snapshot/Facts/Evidence、持久化报告 DAG、结构化 Narrative、发布前 Validator、Markdown/HTML、可选 PDF/Bundle、版本化 API/前端、只读 Discussion、Proposal/Review/Handoff。
- 报告 DAG 以既有 `PipelineJob` 的持久化依赖预创建 `facts -> narrative -> validate -> html -> bundle`；失败上游会阻塞后继，重试同一 Job 后自动继续。
- Evidence 同时保留根制品和字段路径，执行结果引用仅在其 locator 与 SHA 冻结引用一致时标为 `bound`。
- Narrative Agent 只消费冻结 Facts/Evidence。配置 `AUTOAD_REPORT_API_KEY`、`AUTOAD_REPORT_BASE_URL`、`AUTOAD_REPORT_MODEL` 后使用共享结构化 LLM 通道；未配置或调用失败时写入可审计的确定性 fallback 元数据。

剩余工作仅限后续产品迭代，例如更丰富的 Discussion 只读工具和对外部模型的运维配置界面；它们不阻塞当前报告闭环。

## 七、总原则

1. 报告是现有实验控制面的只读投影，不是第二套实验平台。
2. Facts、Evidence、Validation 和制品版本不可覆盖；状态和审阅记录可原子更新并留事件。
3. Markdown/HTML 可审阅不依赖 PDF 成功。
4. LLM 负责解释，不负责创造指标、Attempt、状态、Champion 或预算事实。
5. Report Agent 只能读取受限 typed tools，不能写文件、执行命令或创建实验。
6. 后续实验只能由用户确认的结构化 Proposal 返回现有控制面。
7. 任何新标识符、路径、字段或第三方 API 在实现前必须从实际源码、测试或锁文件核对。
