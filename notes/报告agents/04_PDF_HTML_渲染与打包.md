# 开发计划 R4：HTML、PDF 渲染与打包（HTML 优先）

## 1. 目标

先保证 Markdown、无外部网络依赖的自包含 HTML 和证据包可用；PDF 是后续可选 capability，不得阻断报告阅读和讨论。

## 2. 参考与边界

| 来源 | 可复用机制 | 处理 |
|---|---|---|
| Arbor `src/export.py` | 自包含 HTML、内联 CSS/JS、JSON payload、XSS 转义、超大文本截断 | `[REFER]`，按 AutoAD Facts 重实现 |
| Claw `templates/compiler.py` | `shutil.which()` 预检、timeout、return code、结构化 CompileResult、失败不抛毁主流程 | `[REFER]` / `[REIMPL]`，只重实现编译器外壳 |
| AI-Scientist `perform_writeup.py` | 简单编译循环和输出文件检查 | `[REFER]`；不复制源码 |

## 3. 首版制品

```text
report.md
report.html
report_facts.json
evidence_index.json
report_validation.json
report_digest.json
report_bundle.zip（包含以上文件和 checksums）
```

HTML 成功条件：自包含、可离线打开、数据已转义、引用和状态与同一 `report_id` 绑定。

## 4. HTML Renderer

实现可使用当前 AutoAD 已有依赖；不能假设 Jinja2、Markdown 库或额外模板依赖已经存在。若新增依赖，必须在项目锁文件和验证流程中明确记录。

HTML 需要：

- 内联 CSS 和必要 JS，不使用 CDN；
- HTML 文本和 JSON payload 分别正确转义；
- artifact 和日志遵守大小上限，超限保留尾部并记录截断信息；
- Evidence link 使用 `evidence_id`，不把绝对路径注入页面；
- 浏览器离线打开仍能显示摘要、表格、证据清单和状态。

ReportPage 首版不把生成的 HTML 直接插入当前 DOM，不使用 `dangerouslySetInnerHTML`。HTML 作为下载制品或新窗口打开；如果以后需要内嵌，只能使用 sandboxed iframe，默认不允许脚本、同源权限、顶层导航和弹窗。需要执行自包含 HTML 的交互时，应先另建明确的安全 capability，不把“自包含”当作“可信”。

Arbor 的 base64 payload 只是实现选择，不是必须契约；如果使用 JSON script 节点，必须补充 XSS 和 round-trip 测试。

## 5. PDF Renderer（可选）

PDF 只在独立 `report_render_pdf` Job 中运行：

```text
preflight：检查 xelatex/pdflatex、字体、模板和输出目录
→ render：设置 timeout、capture stdout/stderr
→ validate：检查 return code 和 report.pdf 是否存在且非空
→ format_status.pdf = ready / failed
```

计划不得宣称“xelatex 不可用就可靠降级到 pdflatex”，因为中文模板、字体和包要求不同。实际选择由 capability 预检决定；不可用时输出结构化原因，不改变内容状态。

首版不做自动修改正文的 LaTeX 修复。若未来需要适配 Claw 的 repair 机制，必须限制为安全、可审计的局部修复，并保留每次修改记录。

## 6. Bundle

Bundle 至少包含：

```text
report.md
report.html（如已生成）
report_facts.json
evidence_index.json
report_digest.json
report_validation.json
report_manifest.json
delivery_state_snapshot.json
snapshot.json
bundle_exclusions.json
checksums.sha256
```

`delivery_state_snapshot.json` 是打包时生成的不可变交付快照，至少记录 `report_id`、Snapshot hash、打包时的 content readiness、已纳入的 artifact refs、各格式 readiness、`package_job_id` 和 `packaged_at`。`packaged_at` 固定取 `report_package` Job 首次创建时的 `created_at`，同一 Job 重试时复用，不取每次执行的当前时间。会变化的 `state_revision_at_package` 留在 Bundle 外的 State/Event 或制品交付记录中，不进入确定性 ZIP。Bundle 不携带会继续变化的 live `report_state.json`，也不因后续状态变化而修改已生成的 ZIP。

v1 `report_bundle.zip` 不包含 PDF；PDF 是独立的报告制品和下载项。若以后确实需要“含 PDF 的完整包”，应在 PDF 已 ready 后显式生成另一个 full bundle，而不是让原 Bundle 随 PDF 到达而改变。

Bundle 必须确定性生成：

- 文件按固定的相对路径排序；
- ZIP entry 使用固定 timestamp 和稳定权限；
- 拒绝 symlink、目录逃逸和不在 allow-list 中的文件；
- 默认不包含 checkpoint、数据集、模型权重和原始大日志；
- 已知配置/凭据字段在进入 Bundle 前按结构化规则过滤；
- 写入 `bundle_exclusions.json`，记录被排除制品及原因；
- 相同 report_id、Snapshot hash 和制品字节重复打包得到相同 ZIP hash。
- 交付快照在进入 ZIP 前固定，后续 PDF 或审阅状态变化不改写原 Bundle。

引用的 Evidence 可以进入 `evidence/` 子目录，但必须依据 allow-list 和大小上限复制；不能把整个 run 目录打包。

报告侧制品交付记录中的每个实际制品都要有 artifact ref 和 SHA；不可变 Manifest 只负责身份，State 负责可用制品投影。`checksums.sha256` 校验 Bundle 中除自身外的所有 entry，不循环包含自身。ZIP 生成后再次计算 ZIP 自身 SHA，并把该 SHA 保存在 Bundle 外的 artifact delivery record，不能只记录打包前文件。

## 7. 状态关系

```text
Markdown + validation 通过 → content_ready
HTML 成功                → format_status.html = ready
PDF 成功                 → format_status.pdf = ready
PDF 失败                 → format_status.pdf = failed
```

PDF 或 ZIP 失败不能把已验证的 Markdown 报告改成内容失败。

## 8. 验收

- [ ] HTML 可离线打开，无 CDN 和外部脚本。
- [ ] HTML payload 经过 XSS 转义测试。
- [ ] ReportPage 不直接注入 HTML；sandboxed iframe（若未来启用）默认无脚本和同源权限。
- [ ] 超大日志不会导致 HTML 或 Agent 上下文无限增长。
- [ ] PDF capability 缺失时有明确失败状态和日志。
- [ ] PDF 编译检查 return code、timeout 和输出文件，而非只调用 subprocess。
- [ ] ZIP 包含 Facts、Evidence、Validation 和 checksums。
- [ ] ZIP 显式包含 `bundle_exclusions.json`，并记录排除制品及原因。
- [ ] ZIP 使用 `delivery_state_snapshot.json`，不包含 live `report_state.json`。
- [ ] v1 Bundle 不包含 PDF；PDF 单独下载，不能因 PDF 后生成改变原 Bundle hash。
- [ ] `packaged_at` 来自稳定的 package Job `created_at`，重试不会改变 ZIP hash。
- [ ] `checksums.sha256` 不包含自身，ZIP 自身 SHA 保存在 Bundle 外部交付记录。
- [ ] ZIP 文件排序、timestamp 和内容 hash 稳定，symlink 和敏感/非 allow-list 文件被排除并有记录。
- [ ] 下载包和页面绑定同一个 `report_id`。
- [ ] 重复渲染不覆盖已冻结的正文和 Facts。

## 9. 不做什么

- 不把 PDF 成功作为 Discussion 前置条件。
- 不默认引入 Jinja2、Markdown 转换库或 TeX 发行版。
- 不首版复制 conference 模板、BibTeX 或自动论文修复器。
- 不做 PDF 内嵌浏览器和复杂图表交互。
