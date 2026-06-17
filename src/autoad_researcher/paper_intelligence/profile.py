"""Paper Intelligence permission profiles and skill loading."""

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Permission Profiles
# ---------------------------------------------------------------------------

PAPER_PERMISSION_PROFILES: dict[str, dict[str, list[str]]] = {
    "paper_parse": {
        "allow": [
            "filesystem_read:paper_workspace",
            "filesystem_stat:paper_workspace",
            "document_parse:approved_profile",
        ],
        "deny": [
            "process",
            "shell",
            "python",
            "package_install",
            "network",
            "embedded_pdf_action",
        ],
    },
    "paper_analysis": {
        "allow": [
            "paper_list_sections",
            "paper_read",
            "paper_search",
            "filesystem_read:paper_workspace",
            "web_fetch:approved_public_hosts",
        ],
        "deny": [
            "process",
            "repository_write",
            "arbitrary_remote_uri",
            "latex_compile",
            "python",
            "shell",
            "package_install",
        ],
    },
    "paper_synthesis": {
        "allow": [
            "read:evidence_workspace",
            "read:paper_artifacts",
        ],
        "deny": [
            "document_parse",
            "web_fetch",
            "process",
            "filesystem_write_except_artifacts",
        ],
    },
    "paper_repair": {
        "allow": [
            "paper_read",
            "paper_search",
            "read:evidence_workspace",
            "read:paper_artifacts",
        ],
        "deny": [
            "document_parse",
            "web_fetch",
            "process",
            "repository_write",
            "python",
            "shell",
            "package_install",
        ],
    },
}

# ---------------------------------------------------------------------------
# Model Profiles
# ---------------------------------------------------------------------------


@dataclass
class PaperModelProfile:
    """A paper intelligence model profile with capabilities and routing rules."""

    profile_id: str
    purpose: list[str]
    capabilities: list[str]
    minimum_context_window: int = 0


PAPER_MODEL_PROFILES: dict[str, PaperModelProfile] = {
    "paper_fast_v1": PaperModelProfile(
        profile_id="paper_fast_v1",
        purpose=[
            "section signal extraction",
            "candidate mention extraction",
            "light schema repair",
        ],
        capabilities=["structured_output", "tool_use", "long_context"],
    ),
    "paper_primary_v1": PaperModelProfile(
        profile_id="paper_primary_v1",
        purpose=[
            "paper semantic analysis",
            "method decomposition",
            "conflict interpretation",
            "synthesis",
            "repair",
        ],
        capabilities=[
            "strong_scientific_reasoning",
            "structured_output",
            "tool_use",
            "long_context",
        ],
        minimum_context_window=128000,
    ),
    "paper_fallback_v1": PaperModelProfile(
        profile_id="paper_fallback_v1",
        purpose=[
            "provider outage",
            "one structured output retry",
        ],
        capabilities=["structured_output", "tool_use"],
    ),
}


# ---------------------------------------------------------------------------
# Model Routing Map
# ---------------------------------------------------------------------------

PAPER_MODEL_ROUTING: dict[Literal["fast", "primary", "fallback"], str] = {
    "fast": "deepseek-v4-flash",
    "primary": "deepseek-v4-pro",
    "fallback": "anthropic:claude-sonnet-4-6",
}

# ---------------------------------------------------------------------------
# Stage → Skill Mapping
# ---------------------------------------------------------------------------

PAPER_STAGE_SKILLS: dict[str, str] = {
    "parse": "paper-parse",
    "analysis": "paper-analysis",
    "synthesis": "paper-synthesis",
    "repair": "paper-repair",
}
