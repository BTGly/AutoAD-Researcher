"""Permission adapters for typed V2 dialogue source actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.research_dialogue_agent import SourceInstruction
from autoad_researcher.tools import (
    PermissionDecisionRecord,
    PermissionEngine,
    PermissionProfile,
    PermissionRequest,
    ToolSpec,
)


DIALOGUE_SOURCE_PROFILE = "v2_dialogue_source"


def dialogue_source_permission_engine() -> PermissionEngine:
    """Return the narrow V2 profile for already-registered source actions."""
    return PermissionEngine(
        profiles={
            DIALOGUE_SOURCE_PROFILE: PermissionProfile(
                name=DIALOGUE_SOURCE_PROFILE,
                allow_tools={"paper_parse_mineru"},
                ask_tools={"source_remove"},
            )
        }
    )


def decide_source_action_permission(
    *,
    run_dir: Path,
    action: SourceInstruction,
    source: dict[str, Any],
) -> PermissionDecisionRecord:
    """Evaluate one exact registered-source action without dispatching it."""
    source_id = str(source["source_id"])
    tool = _tool_spec_for(action)
    return dialogue_source_permission_engine().decide(
        PermissionRequest(
            tool_call_id=f"dialogue_{action.action}_{source_id}",
            tool=tool,
            stage="research_chat",
            permission_profile=DIALOGUE_SOURCE_PROFILE,
            arguments_redacted={
                "action": action.action,
                "source_id": source_id,
            },
            active_source_id=source_id,
            cwd_label=(Path("runs") / run_dir.name / "sources" / source_id).as_posix(),
        )
    )


def source_can_reparse(source: dict[str, Any]) -> bool:
    """A V2 reparse requires a registered local PDF source, not just evidence."""
    return (
        source.get("kind") == "paper_pdf"
        and isinstance(source.get("stored_path"), str)
        and bool(source["stored_path"].strip())
    )


def _tool_spec_for(action: SourceInstruction) -> ToolSpec:
    if action.action == "request_source_reparse":
        return ToolSpec(
            name="paper_parse_mineru",
            description="Create a new immutable parse attempt for a registered PDF source.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            read_only=False,
            destructive=False,
            concurrency_safe=False,
            deferred=True,
            permission_category="source_parse",
        )
    return ToolSpec(
        name="source_remove",
        description="Remove a registered source and its derived evidence after confirmation.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        read_only=False,
        destructive=True,
        concurrency_safe=False,
        deferred=False,
        permission_category="source_mutation",
    )
