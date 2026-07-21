from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.renderer_html import render_html


def test_html_renders_markdown_tables_and_escapes_evidence():
    evidence = EvidenceIndex.model_validate({
        "report_id": "report_test", "snapshot_content_sha256": "a" * 64,
        "entries": [{
            "evidence_id": "evidence_test", "evidence_kind": "frozen_session",
            "artifact_ref": {"artifact_id": "x", "artifact_type": "frozen_session", "locator": "reports/x", "sha256": "b" * 64, "size_bytes": 0},
            "source_object_id": "x", "field_path": "$", "summary": "<unsafe>",
        }],
    })
    rendered = render_html(report_id="report_test", markdown="| A | B |\n|---|---|\n| 1 | 2 |", evidence=evidence)
    assert "<table>" in rendered
    assert "&lt;unsafe&gt;" in rendered
    assert "<unsafe>" not in rendered
