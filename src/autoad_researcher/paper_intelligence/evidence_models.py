"""Evidence reference contracts for Paper Intelligence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern


class PaperTextEvidenceRef(BaseModel):
    """Evidence anchored to a text block in the parsed paper body."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["paper_text"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    source_pdf_sha256: str = Field(pattern=Sha256Pattern)

    parse_attempt_id: str = Field(pattern=IdentifierPattern)
    parser_profile_sha256: str = Field(pattern=Sha256Pattern)
    canonical_output_sha256: str = Field(pattern=Sha256Pattern)

    physical_page_index: int = Field(ge=0)
    printed_page_label: str | None = None

    section_path: list[str] = Field(default_factory=list)
    block_id: str = Field(min_length=1)
    bbox: tuple[float, float, float, float] | None = None
    content_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["paper_body_fact"]


class PaperTableEvidenceRef(BaseModel):
    """Evidence anchored to a table in the parsed paper."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["paper_table"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    source_pdf_sha256: str = Field(pattern=Sha256Pattern)
    parse_attempt_id: str = Field(pattern=IdentifierPattern)
    physical_page_index: int = Field(ge=0)
    table_id: str = Field(min_length=1)
    row_id: str | None = None
    column_id: str | None = None
    cell_id: str | None = None
    content_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["paper_table_fact"]


class PaperFigureEvidenceRef(BaseModel):
    """Evidence anchored to a figure in the parsed paper."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["paper_figure"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    source_pdf_sha256: str = Field(pattern=Sha256Pattern)
    parse_attempt_id: str = Field(pattern=IdentifierPattern)
    physical_page_index: int = Field(ge=0)
    figure_id: str = Field(min_length=1)
    caption_block_id: str | None = None
    caption_sha256: str | None = Field(default=None, pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["paper_figure_fact"]


class PaperReferenceEvidenceRef(BaseModel):
    """Evidence anchored to a reference entry in the parsed paper."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["paper_reference"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    source_pdf_sha256: str = Field(pattern=Sha256Pattern)
    parse_attempt_id: str = Field(pattern=IdentifierPattern)
    reference_id: str = Field(min_length=1)
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    content_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["paper_reference_fact"]


class WebPaperEvidenceRef(BaseModel):
    """Evidence from web, arXiv HTML, or AlphaXiv pages (supplementary only)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["web_page", "alpha_xiv_page", "arxiv_html"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    url: str = Field(min_length=1)
    content_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["association_lead", "supplementary_context"]


PaperEvidenceRef = Annotated[
    PaperTextEvidenceRef
    | PaperTableEvidenceRef
    | PaperFigureEvidenceRef
    | PaperReferenceEvidenceRef
    | WebPaperEvidenceRef,
    Field(discriminator="source_kind"),
]


class EvidenceIndexRecord(BaseModel):
    """One append-only Evidence Index record for paper intelligence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    parse_attempt_id: str | None = Field(default=None, pattern=IdentifierPattern)
    evidence: PaperEvidenceRef
