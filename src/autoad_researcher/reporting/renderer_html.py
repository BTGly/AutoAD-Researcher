"""Offline HTML rendering for a validated Markdown report."""

from __future__ import annotations

import html

from markdown_it import MarkdownIt

from autoad_researcher.reporting.evidence import EvidenceIndex

HTML_RENDERER_VERSION = "v2"


def render_html(*, report_id: str, markdown: str, evidence: EvidenceIndex) -> str:
    """Render only generated Markdown plus escaped, report-local Evidence metadata."""

    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    content = renderer.render(markdown)
    evidence_rows = "".join(
        "<li id=\"evidence-" + html.escape(item.evidence_id, quote=True) + "\"><code>"
        + html.escape(item.evidence_id)
        + "</code> <span>"
        + html.escape(item.summary)
        + "</span></li>"
        for item in evidence.entries
    )
    return """<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Research Report</title><style>
body{max-width:960px;margin:2rem auto;padding:0 1rem;font:16px/1.6 system-ui,sans-serif;color:#1f2937}
table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #cbd5e1;padding:.45rem;text-align:left;vertical-align:top}th{background:#f1f5f9}
code{overflow-wrap:anywhere}li{margin:.45rem 0}.meta{color:#475569;font-size:.9rem}
</style></head><body><header><h1>Research Report</h1><p class=\"meta\">Report ID: <code>""" + html.escape(report_id) + """</code></p></header><main>""" + content + """</main>
<section><h2>Evidence</h2><ul>""" + evidence_rows + """</ul></section></body></html>"""
