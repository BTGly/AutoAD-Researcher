"""Deterministic, content-agnostic quality checks for extracted paper text."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field


class TextQualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usable: bool
    character_count: int = Field(ge=0)
    word_like_token_count: int = Field(ge=0)
    natural_language_density: float = Field(ge=0.0, le=1.0)
    page_coverage: float = Field(ge=0.0, le=1.0)
    metadata_ratio: float = Field(ge=0.0, le=1.0)
    repetition_ratio: float = Field(ge=0.0, le=1.0)
    valid_paragraphs: int = Field(ge=0)
    structured_markup_document: bool = False
    warnings: list[str] = Field(default_factory=list)


def assess_extracted_text(
    text: str,
    *,
    page_texts: list[str] | None = None,
) -> TextQualityAssessment:
    """Assess whether extracted text can support paper-content claims."""

    normalized = str(text or "").replace("\x00", " ").strip()
    nonspace = [char for char in normalized if not char.isspace()]
    alphabetic = sum(1 for char in nonspace if char.isalpha())
    natural_density = alphabetic / len(nonspace) if nonspace else 0.0
    tokens = re.findall(r"[^\W\d_]{2,}", normalized, flags=re.UNICODE)

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [line.strip() for line in normalized.splitlines() if line.strip()]
    valid_paragraphs = sum(
        1
        for paragraph in paragraphs
        if len(paragraph) >= 80
        and sum(char.isalpha() for char in paragraph) / max(1, len([char for char in paragraph if not char.isspace()])) >= 0.45
        and len(re.findall(r"[^\W\d_]{2,}", paragraph, flags=re.UNICODE)) >= 8
    )

    lines = [re.sub(r"\s+", " ", line).strip() for line in normalized.splitlines() if line.strip()]
    repetition_ratio = (len(lines) - len(set(lines))) / len(lines) if lines else 0.0
    structured_markup = _is_structured_markup_document(normalized)
    structural_chars = sum(
        len(line)
        for line in lines
        if re.match(r"^(?:%PDF-|\d+\s+\d+\s+obj\b|endobj\b|xref\b|trailer\b|<[/!?A-Za-z])", line)
    )
    tag_chars = sum(len(match.group(0)) for match in re.finditer(r"<[^>]{1,500}>", normalized))
    metadata_ratio = min(1.0, (structural_chars + tag_chars) / max(1, len(normalized)))

    pages = page_texts if page_texts is not None else ([normalized] if normalized else [])
    page_coverage = (
        sum(1 for page in pages if str(page or "").strip()) / len(pages)
        if pages
        else 0.0
    )

    warnings: list[str] = []
    checks = {
        "insufficient_text": len(normalized) < 200,
        "low_natural_language_density": natural_density < 0.45,
        "insufficient_word_like_tokens": len(tokens) < 30,
        "no_valid_paragraphs": valid_paragraphs < 1,
        "low_page_coverage": page_coverage < 0.2,
        "high_metadata_ratio": metadata_ratio > 0.3,
        "high_repetition_ratio": repetition_ratio > 0.65,
        "structured_markup_document": structured_markup,
    }
    warnings.extend(code for code, failed in checks.items() if failed)
    return TextQualityAssessment(
        usable=not warnings,
        character_count=len(normalized),
        word_like_token_count=len(tokens),
        natural_language_density=round(natural_density, 6),
        page_coverage=round(page_coverage, 6),
        metadata_ratio=round(metadata_ratio, 6),
        repetition_ratio=round(repetition_ratio, 6),
        valid_paragraphs=valid_paragraphs,
        structured_markup_document=structured_markup,
        warnings=warnings,
    )


def _is_structured_markup_document(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return False
    try:
        ET.fromstring(stripped)
    except ET.ParseError:
        return False
    return True
