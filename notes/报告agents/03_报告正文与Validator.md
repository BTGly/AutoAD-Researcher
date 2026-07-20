# 开发计划 R3：报告正文与 Validator（对应 PR-R2）

## 1. 目标

在 `ExperimentReportFactsV1`、Evidence Index 和 Digest 基础上生成可信 Markdown。确定性代码负责结构、表格和事实；LLM 只负责解释性文字。Validator 先通过，报告才标记为 `content_ready`。

## 2. 参考依据

| 来源 | 可复用思想 | 限制 |
|---|---|---|
| Arbor `src/report/generator.py` | 普通字符串确定性拼接、缺失输入优雅降级 | 不照搬 Arbor 的 Idea Tree 字段 |
| AI-Scientist `perform_writeup.py` | 逐节写作、提示每节目标、不得臆造结果 | 其源码许可证不允许直接复制 |
| AutoAD `ReflectionAgentFactory` | `response_format` 约束结构化输出 | 不能把 Reflection 结果当作报告事实 |
| ARIS evidence chain | 重要声明保留证据链 | 只吸收概念，不引入其完整图谱 |

采用成熟项目共同体现的最小分工：Arbor 负责确定性结果渲染，AI-Scientist 负责逐节解释，Claw 通过阶段契约检查输出；AutoAD 不把这些项目的自由文本直接当成权威事实。

## 3. 输入边界

LLM 只接收：

```text
ExperimentReportFactsV1
evidence_index.json
report_digest.json
```

默认不传入：

- 完整实验目录；
- 原始 stdout/stderr；
- 完整 notes；
- 未登记的 patch 或任意路径。

需要深查时由 Discussion Agent 的受限 typed tool 读取，报告生成阶段不自由浏览文件系统。

## 4. 确定性与 LLM 的分工

### 确定性生成

- 研究目标、EvaluationContract、数据集和环境；
- repository/commit 和 baseline；
- Idea/Attempt 列表和运行状态；
- primary/guardrail metrics、delta、noise floor；
- failed/non-comparable 列表；
- validity、stop decision、cost；
- evidence 表和 artifact 表；
- 所有数字表格、状态徽章和事实摘要。

### LLM 生成

- 执行摘要的解释性文字；
- 机制解释；
- 假设是否得到支持的自然语言说明；
- 局限、不确定性和下一步理由。

LLM 不得产生新的指标、Attempt ID、状态、Champion、预算、比较结果或 Evidence ID。

## 5. 结构化 Narrative 输出

计划新增 `NarrativeSectionsV1`，每个自然语言 section 是结构化对象而非裸字符串：

```text
section_id
paragraphs:
  - paragraph_id
    prose_template
    claim_ids: list[str]
```

每个段落通过 `claim_ids` 绑定本段声明；没有事实性判断的段落可以为空。`StructuredClaim` 只覆盖事实性声明，不要求每个句子生成 claim：

```text
claim_id
claim_kind
statement_template
fact_refs
evidence_ids
```

`statement_template` 是声明本身的模板，事实值仍通过 placeholder 从 Facts 填入。包含提升、下降、支持、失败、可比性或科学判断的段落必须至少绑定一个 claim；纯方法背景和组织性文字可以不绑定。这样保留段落级可追踪性，不引入逐句 span 图。

数字、状态、Attempt ID、Champion ID 和预算事实在 `prose_template` 中必须使用确定性 fact placeholder，例如：

```text
{{fact:attempt_000003.primary_delta}}
{{fact:champion.candidate_id}}
{{fact:resource.total_gpu_hours}}
```

Renderer 只解析已登记的 placeholder 并从 Facts 填值。这样不需要逐句事实图谱，也不依赖“扫描全文所有数字”的死规则；普通章节号和自然语言仍由模板处理。

固定骨架由代码拼接：

```text
# 研究报告
## 1. 研究摘要
## 2. 研究目标与约束
## 3. 实验配置
## 4. Baseline 与 Champion
## 5. 探索的假设
## 6. 执行结果
## 7. 量化结果
## 8. 失败与不可比较实验
## 9. 科学解释
## 10. 局限与不确定性
## 11. 建议的下一步
## 12. 证据与制品引用
```

标题来自 Facts 或调用参数，不从 `NarrativeSectionsV1` 读取不存在的 `title` 字段。

## 6. Validator

Validator 处理结构化输入和渲染前的 section 对象：

```text
schema_valid
required_sections_present
evidence_ids_exist
artifact_refs_resolve
attempt_ids_exist
failed_attempts_included
non_comparable_not_claimed_as_improvement
baseline_champion_consistent
execution_validity_scientific_status_separated
improvement_respects_existing_scientific_assessment
fact_placeholders_resolve
fact_bearing_claim_has_fact_ref
fact_bearing_paragraph_claims_resolve
no_unbound_numeric_fact_in_template
```

“报告所有数字必须出现在 Facts”不再作为全文正则主校验，因为自由文本中的章节号、日期、版本号、GPU 型号和格式化小数会产生大量误报。事实性数字必须通过 placeholder 和 `fact_refs` 进入 renderer；对非事实性自然语言仍只做低优先级 lint：

```text
unknown_attempt_id
unknown_evidence_id
明显未登记的数值表达
```

未解析 placeholder、没有 fact ref 的事实性 claim 或模板直接携带事实数字属于阻断项；普通 lint 失败不覆盖结构化事实校验结果，但必须记录到 `report_validation.json`。

## 7. 发布与重试

输出：

```text
narrative_sections.json
claim_evidence_map.json
report_validation.json
report.md
```

顺序：

```text
LLM 结构化输出
→ schema validate
→ evidence/status validate
→ 确定性拼接 Markdown
→ 写 validation result
→ 通过后 content_ready
```

LLM 超时、解析错误和 Validator 失败通过持久化报告 Job 重试；每次记录 model、prompt 版本、失败原因和 retry 次数。不得将失败输出当作报告正文发布。

## 8. 验收

- [ ] LLM 不能改变 Facts 中的数字、Attempt、状态和 Champion。
- [ ] LLM 输出错误数字或未登记 placeholder 时，不能进入 `content_ready`。
- [ ] Renderer 对相同 Facts 和相同模板生成相同事实段落。
- [ ] 不存在的 `evidence_id` 被拒绝。
- [ ] 包含事实性判断的段落都能通过 `claim_ids` 解析到声明、Facts 引用和 Evidence；纯背景段落不被强制制造 claim。
- [ ] `NON_COMPARABLE` 不得写成提升或退步。
- [ ] 失败 Attempt 必须进入固定表。
- [ ] baseline/champion 与 Facts 一致。
- [ ] 工程、执行、validity、scientific effect 四类状态不混淆。
- [ ] 缺失字段和部分结果能够生成明确的 partial/inconclusive report。
- [ ] `sections.title` 类似的结构字段错误在 schema/单元测试中被捕获。
- [ ] Validator 失败时不进入 HTML/PDF 发布阶段。

## 9. 不做什么

- 不用正则从自由文本反推所有数字事实。
- 不让 LLM 自由生成完整报告文件。
- 不让 LLM 访问整个 run 目录。
- 不在本阶段生成 PDF、ZIP 或前端交互。
