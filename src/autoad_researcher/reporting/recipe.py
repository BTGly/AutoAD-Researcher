"""Canonical identity for deterministic report-generation behavior."""

from __future__ import annotations

import os

from autoad_researcher.reporting.default_narrative import NARRATIVE_MODEL_PROFILE, NARRATIVE_TEMPLATE_VERSION
from autoad_researcher.reporting.bundle import REPORT_BUNDLE_FORMAT_VERSION
from autoad_researcher.reporting.digest import REPORT_DIGEST_BUILD_VERSION
from autoad_researcher.reporting.evidence import EVIDENCE_INDEX_BUILD_VERSION
from autoad_researcher.reporting.facts import REPORT_FACTS_SCHEMA_VERSION
from autoad_researcher.reporting.narrative import NarrativeSectionsV1
from autoad_researcher.reporting.narrative_agent import NARRATIVE_AGENT_PROFILE, narrative_system_prompt
from autoad_researcher.reporting.pdf import PDF_RENDERER_VERSION
from autoad_researcher.reporting.renderer_html import HTML_RENDERER_VERSION
from autoad_researcher.reporting.renderer_markdown import MARKDOWN_RENDERER_VERSION
from autoad_researcher.reporting.snapshot import canonical_sha256
from autoad_researcher.reporting.validator import REPORT_VALIDATOR_VERSION


def report_generation_profile() -> dict[str, str]:
    """Capture the non-secret provider behavior that changes report content."""

    model = os.environ.get("AUTOAD_REPORT_MODEL", "").strip()
    base_url = os.environ.get("AUTOAD_REPORT_BASE_URL", "").strip().rstrip("/")
    configured = bool(os.environ.get("AUTOAD_REPORT_API_KEY", "").strip() and model and base_url)
    prompt_hash = canonical_sha256(
        {
            "agent_profile": NARRATIVE_AGENT_PROFILE,
            "system_prompt": narrative_system_prompt(),
            "schema": NarrativeSectionsV1.model_json_schema(),
        }
    )
    return {
        "profile_version": "v1",
        "mode": "model" if configured else "deterministic_fallback",
        "model": model if configured else "",
        "provider_base_url": base_url if configured else "",
        "prompt_sha256": prompt_hash,
    }
def report_recipe_hash(generation_profile: dict[str, str] | None = None) -> str:
    """Hash every component whose behavior can change a report version."""

    return canonical_sha256(
        {
            "facts_schema_version": REPORT_FACTS_SCHEMA_VERSION,
            "facts_projections": {
                "evidence_index": EVIDENCE_INDEX_BUILD_VERSION,
                "digest": REPORT_DIGEST_BUILD_VERSION,
            },
            "narrative": {
                "model_profile": NARRATIVE_MODEL_PROFILE,
                "template_version": NARRATIVE_TEMPLATE_VERSION,
                "generation_profile": generation_profile or report_generation_profile(),
            },
            "validator_version": REPORT_VALIDATOR_VERSION,
            "renderers": {
                "html": HTML_RENDERER_VERSION,
                "markdown": MARKDOWN_RENDERER_VERSION,
                "pdf": PDF_RENDERER_VERSION,
                "bundle": REPORT_BUNDLE_FORMAT_VERSION,
            },
        }
    )
