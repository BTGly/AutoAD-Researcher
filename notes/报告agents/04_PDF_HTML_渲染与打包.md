# 开发计划 R4：HTML、PDF 渲染与打包（HTML 优先）

## 1. 目标

先保证 Markdown、无外部网络依赖的自包含 HTML 和证据包可用；PDF 是后续可选 capability，不得阻断报告阅读和讨论。

## 2. 参考与边界

| 来源 | 可复用机制 | 处理 |
|---|---|---|
| Arbor `src/export.py` | 自包含 HTML、内联 CSS/JS、JSON payload、XSS 转义、超大文本截断 | `[REFER]`，按 AutoAD Facts 重实现 |
| Claw `templates/compiler.py` | `shutil.which()` 预检、timeout、return code、结构化 CompileResult、失败不抛毁主流程 | `[ADAPT]`，只适配编译器外壳 |
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
report.html
report_facts.json
evidence_index.json
report_digest.json
report_validation.json
report_manifest.json
checksums.sha256
```

`report.html` 是 Bundle 的必需项；HTML Job 失败时 Bundle Job 不得发布。PDF 仍是可选 format，不影响已验证的 Markdown/HTML 内容。

引用的 Evidence 可以进入 `evidence/` 子目录，但必须依据 allow-list 和大小上限复制；不能把整个 run 目录打包。

Manifest 中每个实际制品都要有 artifact ref 和 SHA，并使用现有 canonical helper 复核 manifest 自身的摘要。ZIP 生成后再次计算 ZIP 自身 SHA，不能只记录打包前文件。

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
- [ ] 超大日志不会导致 HTML 或 Agent 上下文无限增长。
- [ ] PDF capability 缺失时有明确失败状态和日志。
- [ ] PDF 编译检查 return code、timeout 和输出文件，而非只调用 subprocess。
- [ ] ZIP 包含 Markdown、必需的 HTML、Facts、Evidence、Validation 和 checksums。
- [ ] 下载包和页面绑定同一个 `report_id`。
- [ ] 重复渲染不覆盖已冻结的正文和 Facts。

## 9. 不做什么

- 不把 PDF 成功作为 Discussion 前置条件。
- 不默认引入 Jinja2、Markdown 转换库或 TeX 发行版。
- 不首版复制 conference 模板、BibTeX 或自动论文修复器。
- 不做 PDF 内嵌浏览器和复杂图表交互。
