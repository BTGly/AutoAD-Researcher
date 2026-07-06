"""Parser contracts for MinerU / document parsing."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern, validate_workspace_path


class PageRange(BaseModel):
    """A page range for targeted parsing."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)


class DocumentParseRequest(BaseModel):
    """Request to parse a PDF with a locked MinerU profile."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    source_pdf_path: str
    parser_profile_id: str = Field(pattern=IdentifierPattern)
    page_range: PageRange | None = None
    ocr_policy: Literal["auto", "disabled", "forced"]
    language_hints: list[str] = Field(default_factory=list)
    max_pages: int = Field(ge=0)
    max_runtime_seconds: int = Field(ge=0)

    @field_validator("source_pdf_path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_workspace_path(value)


class DocumentParseResult(BaseModel):
    """Result of a MinerU parse attempt."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    parse_attempt_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    parser_manifest_path: str
    canonical_output_path: str
    parse_quality_report_path: str
    status: Literal["success", "partial_success", "failed"]
    warnings: list[str] = Field(default_factory=list)


class ParserManifest(BaseModel):
    """Locked parser identity and configuration manifest."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    parser_name: Literal["MinerU"]
    parser_version: str = Field(min_length=1)
    parser_backend: Literal["pipeline", "hybrid", "vlm"]
    parser_profile_id: str = Field(pattern=IdentifierPattern)
    parser_profile_sha256: str = Field(pattern=Sha256Pattern)

    model_revision: str | None = None
    model_weight_sha256: str | None = Field(default=None, pattern=Sha256Pattern)
    ocr_engine: str | None = None
    ocr_language_hints: list[str] = Field(default_factory=list)

    runtime_python_version: str = Field(min_length=1)
    runtime_platform: str = Field(min_length=1)
    device_profile: Literal["cpu", "cuda", "mps", "unknown"]

    source_pdf_sha256: str = Field(pattern=Sha256Pattern)
    canonical_output_sha256: str = Field(pattern=Sha256Pattern)


class ParseQualityReport(BaseModel):
    """Quality assessment of a parse output."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    status: Literal["success", "partial_success", "failed"]
    parse_attempt_id: str | None = Field(default=None, pattern=IdentifierPattern)
    source_id: str | None = Field(default=None, pattern=IdentifierPattern)
    parser: str | None = Field(default=None, pattern=IdentifierPattern)
    quality_level: Literal["usable", "partial", "unusable"] | None = None
    usable_for: list[str] = Field(default_factory=list)
    not_usable_for: list[str] = Field(default_factory=list)
    page_count: int = Field(ge=0)
    empty_pages: list[int] = Field(default_factory=list)
    scanned_pages: list[int] = Field(default_factory=list)
    ocr_pages: list[int] = Field(default_factory=list)
    low_confidence_pages: list[int] = Field(default_factory=list)
    garbled_pages: list[int] = Field(default_factory=list)
    table_parse_warnings: list[str] = Field(default_factory=list)
    formula_parse_warnings: list[str] = Field(default_factory=list)
    figure_parse_warnings: list[str] = Field(default_factory=list)
    fatal_errors: list[str] = Field(default_factory=list)
