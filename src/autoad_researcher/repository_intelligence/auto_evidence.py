"""Automatic evidence middleware for filesystem tool calls."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.repository_intelligence.evidence import (
    FileEvidenceRequest,
    append_evidence,
    create_file_evidence,
)
from autoad_researcher.repository_intelligence.evidence_models import FileEvidenceRef
from autoad_researcher.tools import (
    FilesystemReadRequest,
    FilesystemSearchRequest,
    FilesystemToolResult,
    PermissionEngine,
    filesystem_read,
    filesystem_search,
)


class FilesystemEvidenceResult(BaseModel):
    """Filesystem tool result plus evidence refs generated from it."""

    model_config = ConfigDict(extra="forbid")

    tool_result: FilesystemToolResult
    evidence: list[FileEvidenceRef] = Field(default_factory=list)


def filesystem_read_with_evidence(
    request: FilesystemReadRequest,
    *,
    permission_engine: PermissionEngine,
    evidence_index_path: Path,
    evidence_id: str,
) -> FilesystemEvidenceResult:
    """Run filesystem_read and append file evidence when active repository context exists."""
    result = filesystem_read(request, permission_engine=permission_engine)
    evidence: list[FileEvidenceRef] = []
    context = request.tool_context.active_repository if request.tool_context else None
    if result.status == "success" and result.text is not None and context is not None:
        line_count = max(1, len(result.text.splitlines()))
        ref = create_file_evidence(
            context=context,
            request=FileEvidenceRequest(
                evidence_id=evidence_id,
                path=request.path,
                start_line=1,
                end_line=line_count,
                tool_call_id=request.tool_call_id,
            ),
        )
        append_evidence(evidence_index_path, ref)
        evidence.append(ref)
    return FilesystemEvidenceResult(tool_result=result, evidence=evidence)


def filesystem_search_with_evidence(
    request: FilesystemSearchRequest,
    *,
    permission_engine: PermissionEngine,
    evidence_index_path: Path,
    evidence_id_prefix: str,
) -> FilesystemEvidenceResult:
    """Run filesystem_search and append one line-range evidence ref per match."""
    result = filesystem_search(request, permission_engine=permission_engine)
    evidence: list[FileEvidenceRef] = []
    context = request.tool_context.active_repository if request.tool_context else None
    if result.status == "success" and context is not None:
        for index, match in enumerate(result.matches, 1):
            ref = create_file_evidence(
                context=context,
                request=FileEvidenceRequest(
                    evidence_id=f"{evidence_id_prefix}_{index:03d}",
                    path=match.path,
                    start_line=match.line_number,
                    end_line=match.line_number,
                    tool_call_id=request.tool_call_id,
                ),
            )
            append_evidence(evidence_index_path, ref)
            evidence.append(ref)
    return FilesystemEvidenceResult(tool_result=result, evidence=evidence)
