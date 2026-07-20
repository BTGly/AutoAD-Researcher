# AutoAD 报告 Agents 设计与开发计划文档索引（修订版 v0.3）

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

每条复用记录必须能回答：来源仓库 URL、固定 commit、上游实际文件路径、许可证及 license hash、复制还是重写、AutoAD 的本地差异、是否需要 NOTICE。`planned_local_targets` 只是未来落点，不声称这些文件当前已存在；实施时仍需从当前仓库精确确认目标模块。这里的“已核对”限定为固定上游提交中的文件和行为；本地 checkout 的额外修改、依赖环境和未提交内容仍需实施时复核。完整记录见 `reference_reuse_manifest.yaml`。没有这些信息时只能标 `[REFER]`。

## 三、已核对的成熟项目

| 项目 | 仓库与固定版本 | 可吸收的机制 | 本计划中的处理 |
|---|---|---|---|
| Arbor | `https://github.com/RUC-NLPIR/Arbor` @ `4f8c5c2e...`；Apache-2.0 | partial `REPORT.md`、确定性拼接、自包含 HTML、artifact export、独立只读 Companion | `[REFER]`；HTML/Companion 可按需 `[ADAPT]`，不直接复制 |
| Claw-AI-Lab | `https://github.com/Claw-AI-Lab/Claw-AI-Lab` @ `84553208...`；README 声明 MIT，固定 checkout 未发现 LICENSE 文件 | 阶段输入/输出契约、artifact manifest、编译预检、timeout、非阻塞 LaTeX、PIVOT/REFINE 版本化 | `[REFER]` / `[REIMPL]`；许可证未核验前不复制代码 |
| AI-Scientist | `https://github.com/SakanaAI/AI-Scientist` @ `1de1dbc1...`；专用 Source Code License | 逐节写作、结果不得臆造、编译和引用检查 | `[REFER]`；不复制源码 |
| AiScientist | `https://github.com/AweAI-Team/AiScientist` @ `6bba373d...`；MIT | MIT 许可下的科研工作流和报告组织思路 | `[REFER]`，不形成运行时依赖 |
| DeepAgents | `https://github.com/langchain-ai/deepagents` @ `59755031...`；MIT | `response_format`、filesystem permission、`HarnessProfile.excluded_tools`、持久化图状态 API | `[REFER]`；按锁定版本核对 API，不按旧路径硬写 |
| ARIS | `references/research-automation/Auto-claude-code-research-in-sleep/`；本地参考，无可复用 Git 身份 | 原子 JSON、单运行锁、可恢复队列、`done` 与 `accepted` 分离 | `[REFER]` / `[REIMPL]` |
| AutoSOTA | `https://github.com/tsinghua-fib-lab/AutoSOTA` @ `c480ce24...`；MIT | inspect/report CLI 和多格式查看思路 | `[REFER]` |

`references/research-automation/Arbor`、`Claw-AI-Lab`、`AiScientist` 是指向 `/root/autodl-tmp/repos/` 的断链；实施时使用上表的仓库 URL、固定 commit 和仓库内相对源文件，不引用断链路径。

## 四、AutoAD 现有能力（直接接入，不重新设计）

| 组件 | 实际位置 | 报告侧用途 |
|---|---|---|
| `ExperimentSession` / `ExperimentSessionStore` | `src/autoad_researcher/experiment/` | Session 身份、状态、合同和原子存储 |
| `PipelineJobStore` 服务函数 | `src/autoad_researcher/assistant/v2/job_service.py` | 直接复用当前 Job 的幂等、claim、complete、fail 和 stale-running 恢复；报告专用 failed requeue 是 PR-R0B 的新增受限操作 |
| `EventStore` / V2 event service | `src/autoad_researcher/core/events.py`、`src/autoad_researcher/assistant/v2/event_service.py` | 阶段事件和锁内 JSONL 追加 |
| `ExperimentAttemptService` | `src/autoad_researcher/experiment/attempt_service.py` | confirmatory/retry 的 Attempt/Job 入口 |
| `Coordinator` / `IdeaTreeStore` / `ExecutorAgent` | `src/autoad_researcher/experiment/` | REFINE 的 Idea、Intervention、patch 和实现证据闭环 |
| `OutcomeCard` / `EffectiveScientificAssessment` | `src/autoad_researcher/experiment/` | 执行/协议事实和科学比较事实 |
| `ArtifactReferenceV2` / `ResourceUsageReport` | `src/autoad_researcher/schemas/` | 带 SHA 的 artifact 和计算资源事实 |
| `CandidateRegistry` / `ChampionPointer` | `src/autoad_researcher/experiment/promotion.py` | Champion 和候选指针，Snapshot 时冻结小对象 |
| `CognitiveCostSummary` | `src/autoad_researcher/experiment/cost_summary.py` | LLM 调用、token 和认知预算，不代表 GPU 资源 |
| `StopDecision` | `src/autoad_researcher/experiment/stop_policy.py` | 停止事实 |
| `TaskBridge` | `src/autoad_researcher/assistant/v2/task_bridge.py` | PIVOT 的新 Run/Task/Session 入口，但受 `input_task.yaml` 单次物化约束 |
| `ReportPage` / `report_route` | `frontend/src/components/ReportPage.tsx`、`src/autoad_researcher/server/routes/report_route.py` | 现有兼容报告入口，原地升级 |

旧的 `ReportFacts` 只保留兼容读取，不直接扩展为新报告事实契约。

## 五、文档与实施顺序

| 文档 | 主题 | 对应实施阶段 |
|---|---|---|
| `01_报告Schema与生命周期.md` | 来源适配、冻结 Snapshot、Manifest/State、Job 契约 | PR-R0A / PR-R0B |
| `02_Facts与Evidence索引.md` | Snapshot、Facts、Evidence、Digest | PR-R1 |
| `03_报告正文与Validator.md` | Narrative、Validator、Markdown | PR-R2 |
| `04_PDF_HTML_渲染与打包.md` | HTML 优先，PDF/Bundle 后置 | PR-R3 / PR-R6 |
| `05_报告API与状态接口.md` | API、下载和兼容路由 | PR-R3 |
| `06_ReportPage交互式前端.md` | 最小报告工作区 | PR-R3 |
| `07_ReportDiscussionAgent与Propose模式.md` | 只读 Discussion 和 Propose | PR-R4 / PR-R5 |
| `08_转交_版本与审阅.md` | Review、Proposal、Handoff、lineage | PR-R5 |
| `reference_reuse_manifest.yaml` | 外部项目版本、许可证和文件级复用记录 | 全阶段文档 |
| `开发大纲.md` | 总体架构、测试和验收 | 全阶段 |

最终顺序：

```text
PR-R0A 权威来源适配 + 同步冻结 Snapshot
→ PR-R0B Report identity + Manifest/State + Job 控制面
→ PR-R1 Facts + Evidence
→ PR-R2 Narrative + Validator + Markdown
→ PR-R3 API + 最小前端 + HTML
→ PR-R4 Read-only Discussion
→ PR-R5 Proposal + Review + Handoff
→ PR-R6 可选 PDF + Bundle
```

## 六、总原则

1. 报告是现有实验控制面的只读投影，不是第二套实验平台。
2. Facts、Evidence、Validation 和制品版本不可覆盖；状态和审阅记录可原子更新并留事件。
3. Markdown/HTML 可审阅不依赖 PDF 成功。
4. LLM 负责解释，不负责创造指标、Attempt、状态、Champion 或预算事实。
5. Report Agent 只能读取受限 typed tools，不能写文件、执行命令或创建实验。
6. 后续实验只能由用户确认的结构化 Proposal 返回现有控制面。
7. 任何新标识符、路径、字段或第三方 API 在实现前必须从实际源码、测试或锁文件核对。
8. Snapshot 冻结会变化的小型控制面对象；大型制品只保存带 SHA 的引用。
9. 科学比较统一经过 `EffectiveScientificAssessment`；认知成本和计算资源分开记录。
10. Report Agent 的解释可以使用事实占位符，但最终数字和状态由确定性 Renderer 填充。
