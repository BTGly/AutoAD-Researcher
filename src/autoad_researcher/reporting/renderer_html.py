"""Offline self-contained HTML wrapper for an already validated report."""

from __future__ import annotations

import html

HTML_RENDERER_VERSION = "v1"


def render_html(*, report_id: str, markdown: str) -> str:
    """Preserve Markdown as escaped text; no CDN, scripts, or untrusted HTML."""

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research Report</title><style>body{max-width:960px;margin:2rem auto;padding:0 1rem;font:16px/1.6 system-ui,sans-serif;color:#1f2937}pre{white-space:pre-wrap;background:#f8fafc;padding:1rem;border:1px solid #e2e8f0;border-radius:.5rem}</style></head>
<body><h1>Research Report</h1><p>Report ID: <code>""" + html.escape(report_id) + """</code></p><pre>""" + html.escape(markdown) + """</pre></body></html>"""
