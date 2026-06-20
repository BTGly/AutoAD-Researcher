"""PatchMaterializer — Step 3.6 read-only payload generation.

Produces PatchPayload objects and PatchPayloadManifest from a
RepositoryChangePlan and narrow repository content.
Writes payload bytes to ArtifactStore (not working tree).
"""

import hashlib
from pathlib import Path
from typing import Optional

from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.schemas.patch_planning import (
    NarrowRepositoryReadRequest,
    PatchPayload,
    PatchPayloadManifest,
    PlannedRepositoryChange,
    RepositoryChangePlan,
    canonical_sha,
)
from autoad_researcher.code_agent.narrow_repo_read import NarrowRepositoryReader


class PatchMaterializer:
    """Read-only agent that produces PatchPayload and PatchPayloadManifest.

    Contract:
      - Reads file content from a narrow repository context (Path root)
      - For each change with a non-None payload_id, reads current content,
        computes before_sha256, generates proposed content, writes to ArtifactStore
      - Changes without payload_id are skipped
      - Output is a PatchPayloadManifest with all payloads + proposed diff
      - Does NOT write to the repository working tree
    """

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self._store = artifact_store

    def materialize(
        self,
        *,
        plan: RepositoryChangePlan,
        repository_root: Path,
        run_id: str,
        narrow_request: Optional[NarrowRepositoryReadRequest] = None,
    ) -> list[PatchPayload]:
        """Produce PatchPayload list from plan changes.

        Only materializes changes that have a non-None payload_id.
        Reads current file content from repository_root for before_sha256.
        Writes generated payload bytes to ArtifactStore.

        If narrow_request is provided, file access is constrained by
        NarrowRepositoryReader (allowed_paths, max_files, max_bytes).
        """
        reader: Optional[NarrowRepositoryReader] = None
        if narrow_request is not None:
            reader = NarrowRepositoryReader(narrow_request, repository_root)

        payloads: list[PatchPayload] = []
        workspace_changes = [c for c in plan.changes
                              if c.payload_id is not None
                              and c.operation_kind in {"create", "modify", "rename"}]

        for change in workspace_changes:
            payload = self._materialize_change(change, repository_root, run_id, reader)
            if payload is not None:
                payloads.append(payload)

        return payloads

    def _read_file(
        self,
        path: Path,
        relative: str,
        reader: Optional[NarrowRepositoryReader],
    ) -> Optional[bytes]:
        """Read file content, optionally through NarrowRepositoryReader."""
        if reader is not None:
            try:
                return reader.read_file(relative)
            except (PermissionError, FileNotFoundError):
                return None
        if path.exists():
            return path.read_bytes()
        return None

    def _materialize_change(
        self,
        change: PlannedRepositoryChange,
        repository_root: Path,
        run_id: str,
        reader: Optional[NarrowRepositoryReader] = None,
    ) -> Optional[PatchPayload]:
        """Produce a single PatchPayload for one change.

        For modify/rename: reads current file, computes before_sha256.
        For create: no before_sha256 (file doesn't exist yet).
        Writes payload bytes to ArtifactStore.
        """
        file_path = repository_root / change.repository_path
        before_content: Optional[bytes] = None
        before_sha256: Optional[str] = None

        if change.operation_kind in {"modify", "rename"}:
            before_content = self._read_file(file_path, change.repository_path, reader)
            if before_content is None:
                return None
            before_sha256 = hashlib.sha256(before_content).hexdigest()

        proposed_content = self._generate_proposed_content(change, before_content)
        payload_content = proposed_content or b""
        payload_sha256 = hashlib.sha256(payload_content).hexdigest()

        if change.payload_id is None:
            return None

        payload_filename = f"payload_{change.payload_id}.bin"
        self._store.write_raw(run_id, payload_filename, payload_content)

        return PatchPayload(
            payload_id=change.payload_id,
            change_id=change.change_id,
            payload_kind="full_after_content",
            payload_media_type="application/octet-stream",
            payload_size_bytes=len(payload_content),
            before_sha256=before_sha256,
            target_before_sha256=change.target_before_sha256,
            target_path=change.repository_path,
            payload_artifact_id=payload_filename,
            payload_sha256=payload_sha256,
        )

    @staticmethod
    def _generate_proposed_content(
        change: PlannedRepositoryChange,
        before_content: Optional[bytes],
    ) -> bytes:
        """Generate proposed file content via code transformation.

        For modify operations with a known symbol_delta.symbol_name,
        applies a target code transformation. For create operations,
        generates a minimal file.
        Falls back to unchanged content when no transformation applies.
        """
        if change.operation_kind == "create":
            return f"# {change.repository_path}\n".encode("utf-8")

        if change.operation_kind in {"modify", "rename"} and before_content:
            return PatchMaterializer._apply_code_transformation(
                change, before_content
            )

        return before_content or b""

    @staticmethod
    def _apply_code_transformation(
        change: PlannedRepositoryChange,
        before_content: bytes,
    ) -> bytes:
        """Apply code transformation based on change plan metadata.

        Currently handles:
        - symbol_name == "_compute_greedy_coreset_indices":
          injects a coreset size clamp to prevent OOM
        """
        text = before_content.decode("utf-8")
        symbol = None
        if change.symbol_delta:
            symbol = change.symbol_delta.symbol_name

        if symbol == "_compute_greedy_coreset_indices":
            old_line = "        num_coreset_samples = int(len(features) * self.percentage)"
            new_lines = (
                "        num_coreset_samples = int(len(features) * self.percentage)\n"
                "        # AutoAD: clamp coreset size to prevent OOM on large feature banks\n"
                "        num_coreset_samples = min(num_coreset_samples, 10000)\n"
            )
            if old_line in text:
                text = text.replace(old_line, new_lines)

        return text.encode("utf-8")


def build_payload_manifest(
    *,
    run_id: str,
    workspace_id: str,
    patch_plan_sha256: str,
    payloads: list[PatchPayload],
    proposed_diff_artifact_id: str,
    proposed_diff_sha256: str,
    manifest_id: Optional[str] = None,
) -> PatchPayloadManifest:
    """Build a PatchPayloadManifest from materialized payloads."""
    manifest = PatchPayloadManifest(
        manifest_id=manifest_id or f"manifest_{run_id}_{workspace_id}",
        run_id=run_id,
        workspace_id=workspace_id,
        patch_plan_sha256=patch_plan_sha256,
        payloads=payloads,
        proposed_diff_artifact_id=proposed_diff_artifact_id,
        proposed_diff_sha256=proposed_diff_sha256,
        manifest_sha256="0" * 64,
    )
    manifest.manifest_sha256 = canonical_sha(manifest)
    return manifest
