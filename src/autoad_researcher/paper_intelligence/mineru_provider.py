"""Locked MinerU parser provider.

Provides version-locked, profile-locked PDF parsing. Supports both real
MinerU backend and fixture-based parse simulation for offline testing.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autoad_researcher.paper_intelligence.ids import Sha256Pattern
from autoad_researcher.paper_intelligence.parser_models import (
    DocumentParseRequest,
    DocumentParseResult,
    ParserManifest,
    ParseQualityReport,
)


class MinerUProvider(Protocol):
    """Interface for a locked MinerU parser provider.

    All implementations must be version-locked and profile-locked
    to ensure reproducible evidence.
    """

    def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        """Parse a PDF document with a locked MinerU profile."""
        ...

    def get_manifest(self, result: DocumentParseResult) -> ParserManifest:
        """Generate the locked parser manifest for a parse attempt."""
        ...

    def get_quality_report(self, result: DocumentParseResult) -> ParseQualityReport:
        """Generate a parse quality report."""
        ...

    @property
    def parser_version(self) -> str:
        """Locked MinerU package version."""
        ...

    @property
    def parser_profile_id(self) -> str:
        """Active parse profile identifier."""
        ...


@dataclass
class MinerUProfileConfig:
    """Locked MinerU profile configuration.

    Every field must be pinned to ensure reproducible evidence.
    """

    profile_id: str
    parser_version: str
    parser_backend: str  # pipeline, hybrid, vlm
    model_revision: str | None
    model_weight_sha256: str | None
    ocr_engine: str | None
    ocr_language_hints: list[str]

    def compute_profile_sha256(self) -> str:
        """Compute a stable SHA256 of the profile configuration."""
        import hashlib

        parts = [
            self.profile_id,
            self.parser_version,
            self.parser_backend,
            self.model_revision or "",
            self.model_weight_sha256 or "",
            self.ocr_engine or "",
            ",".join(sorted(self.ocr_language_hints)),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


class FixtureMinerUProvider:
    """Test-fixture MinerU provider that simulates parsing.

    Generates deterministic parse output artifacts from fixture data
    without requiring a real MinerU installation. Used for CI testing.
    """

    def __init__(
        self,
        profile: MinerUProfileConfig,
        runtime_python_version: str,
        runtime_platform: str,
        device_profile: str,
    ):
        self._profile = profile
        self._runtime_python_version = runtime_python_version
        self._runtime_platform = runtime_platform
        self._device_profile = device_profile

    @property
    def parser_version(self) -> str:
        return self._profile.parser_version

    @property
    def parser_profile_id(self) -> str:
        return self._profile.profile_id

    def parse(self, request: DocumentParseRequest) -> DocumentParseResult:
        """Simulate a parse attempt using fixture data.

        In real operation this calls MinerU. The fixture version simulates
        a successful parse when the source PDF path exists.
        """
        import uuid

        source_path = Path(request.source_pdf_path)
        if not source_path.exists():
            return DocumentParseResult(
                schema_version=1,
                parse_attempt_id=f"pa_{uuid.uuid4().hex[:12]}",
                source_id=request.source_id,
                parser_manifest_path="",
                canonical_output_path="",
                parse_quality_report_path="",
                status="failed",
                warnings=["source PDF not found"],
            )

        if not source_path.suffix.lower() == ".pdf":
            return DocumentParseResult(
                schema_version=1,
                parse_attempt_id=f"pa_{uuid.uuid4().hex[:12]}",
                source_id=request.source_id,
                parser_manifest_path="",
                canonical_output_path="",
                parse_quality_report_path="",
                status="failed",
                warnings=["source file is not a PDF"],
            )

        return DocumentParseResult(
            schema_version=1,
            parse_attempt_id=f"pa_{uuid.uuid4().hex[:12]}",
            source_id=request.source_id,
            parser_manifest_path="",
            canonical_output_path="",
            parse_quality_report_path="",
            status="success",
            warnings=[],
        )

    def get_manifest(self, result: DocumentParseResult) -> ParserManifest:
        """Generate a locked parser manifest."""
        import hashlib

        dummy = hashlib.sha256(b"fixture").hexdigest()
        return ParserManifest(
            schema_version=1,
            parser_name="MinerU",
            parser_version=self._profile.parser_version,
            parser_backend=self._profile.parser_backend,
            parser_profile_id=self._profile.profile_id,
            parser_profile_sha256=self._profile.compute_profile_sha256(),
            model_revision=self._profile.model_revision,
            model_weight_sha256=self._profile.model_weight_sha256,
            ocr_engine=self._profile.ocr_engine,
            ocr_language_hints=list(self._profile.ocr_language_hints),
            runtime_python_version=self._runtime_python_version,
            runtime_platform=self._runtime_platform,
            device_profile=self._device_profile,
            source_pdf_sha256=dummy,
            canonical_output_sha256=dummy,
        )

    def get_quality_report(self, result: DocumentParseResult) -> ParseQualityReport:
        """Generate a fixture parse quality report."""
        if result.status == "failed":
            return ParseQualityReport(
                schema_version=1,
                status="failed",
                page_count=0,
                fatal_errors=result.warnings,
            )
        return ParseQualityReport(
            schema_version=1,
            status="success",
            page_count=12,
        )


MINERU_PIPELINE_V1_PROFILE = MinerUProfileConfig(
    profile_id="mineru_pipeline_v1",
    parser_version="0.10.0",
    parser_backend="pipeline",
    model_revision=None,
    model_weight_sha256=None,
    ocr_engine=None,
    ocr_language_hints=["en"],
)
