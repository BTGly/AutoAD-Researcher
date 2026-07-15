from autoad_researcher.paper_intelligence.text_quality import assess_extracted_text


def test_research_prose_passes_multi_signal_quality_gate():
    text = "\n\n".join([
        "This study evaluates a reproducible systems method under a fixed protocol. "
        "We describe the implementation, controlled variables, hardware environment, and evaluation procedure in detail.",
        "The experiments compare the reference implementation with the proposed configuration. "
        "Results include correctness checks, latency distributions, memory measurements, and repeated-trial variance.",
    ])

    assessment = assess_extracted_text(text, page_texts=[text[:180], text[180:]])

    assert assessment.usable is True
    assert assessment.valid_paragraphs == 2
    assert assessment.page_coverage == 1.0


def test_xml_metadata_document_is_not_usable_paper_text():
    text = (
        "<?xml version='1.0'?><metadata><title>Example document</title>"
        "<description>This metadata record describes a generated PDF and its editing software. "
        "It is deliberately long enough to resemble prose but is not extracted paper body content.</description>"
        "<creator>Document Tool</creator></metadata>"
    )

    assessment = assess_extracted_text(text)

    assert assessment.usable is False
    assert assessment.structured_markup_document is True
    assert "structured_markup_document" in assessment.warnings


def test_pdf_object_ascii_is_not_usable_paper_text():
    text = "%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\nxref\n0 2\ntrailer <</Root 1 0 R>>\n%%EOF"

    assessment = assess_extracted_text(text)

    assert assessment.usable is False
    assert "insufficient_text" in assessment.warnings
    assert assessment.metadata_ratio > 0.3
