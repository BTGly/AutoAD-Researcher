"""Canonical identity for deterministic report-generation behavior."""

from __future__ import annotations

from autoad_researcher.reporting.default_narrative import NARRATIVE_MODEL_PROFILE, NARRATIVE_TEMPLATE_VERSION
from autoad_researcher.reporting.facts import REPORT_FACTS_SCHEMA_VERSION
from autoad_researcher.reporting.renderer_html import HTML_RENDERER_VERSION
from autoad_researcher.reporting.renderer_markdown import MARKDOWN_RENDERER_VERSION
from autoad_researcher.reporting.snapshot import canonical_sha256
from autoad_researcher.reporting.validator import REPORT_VALIDATOR_VERSION


def report_recipe_hash() -> str:
    """Hash every component whose behavior can change a report version."""

    return canonical_sha256(
        {
            "facts_schema_version": REPORT_FACTS_SCHEMA_VERSION,
            "narrative": {
                "model_profile": NARRATIVE_MODEL_PROFILE,
                "template_version": NARRATIVE_TEMPLATE_VERSION,
            },
            "validator_version": REPORT_VALIDATOR_VERSION,
            "renderers": {
                "html": HTML_RENDERER_VERSION,
                "markdown": MARKDOWN_RENDERER_VERSION,
            },
        }
    )
