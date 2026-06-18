"""PatchMaterializer — Step 3.6 read-only payload generation.

Produces PatchPayload objects and PatchPayloadManifest from a
RepositoryChangePlan and narrow repository content.
Does NOT write to the working tree.
"""

import hashlib
from pathlib import Path
from typing import Optional

from autoad_researcher.schemas.patch_planning import (
    PatchPayload, PatchPayloadManifest, PlannedRepositoryChange,
    RepositoryChangePlan,
)


class PatchMaterializer:
    """Read-only agent that produces PatchPayload and PatchPayloadManifest.

    Contract:
      - Reads file content from a narrow repository context (Path root)
      - For each change with a non-None payload_id, reads current content,
        computes before_sha256, and produces a payload with full_after_content
      - Changes without payload_id (delete, configuration-only) are skipped
      - Output is a PatchPayloadManifest with all payloads + proposed diff
      - Does NOT write to the repository working tree
    """

    def __init__(self) -> None:
        pass

    def materialize(
        self,
        *,
        plan: RepositoryChangePlan,
        repository_root: Path,
        run_id: str,
    ) -> list[PatchPayload]:
        """Produce PatchPayload list from plan changes.

        Only materializes changes that have a non-None payload_id (needs payload).
        Reads current file content from repository_root for before_sha256.
        """
        payloads: list[PatchPayload] = []
        workspace_changes = [c for c in plan.changes
                             if c.payload_id is not None
                             and c.operation_kind in {"create", "modify", "rename"}]

        for change in workspace_changes:
            payload = self._materialize_change(change, repository_root)
            if payload is not None:
                payloads.append(payload)

        return payloads

    def _materialize_change(
        self,
        change: PlannedRepositoryChange,
        repository_root: Path,
    ) -> Optional[PatchPayload]:
        """Produce a single PatchPayload for one change.

        For modify/rename: reads current file, computes before_sha256.
        For create: no before_sha256 (file doesn't exist yet).
        """
        file_path = repository_root / change.repository_path
        before_content: Optional[bytes] = None
        before_sha256: Optional[str] = None

        if change.operation_kind in {"modify", "rename"}:
            if file_path.exists():
                before_content = file_path.read_bytes()
                before_sha256 = hashlib.sha256(before_content).hexdigest()
            else:
                return None

        # Generate proposed content (placeholder for MVP — real code synthesis
        # to be replaced by LLM-driven payload generation)
        proposed_content = self._generate_proposed_content(change, before_content)
        payload_content = proposed_content or b""
        payload_sha256 = hashlib.sha256(payload_content).hexdigest()

        # Create a deterministic payload artifact path (for ArtifactStore)
        payload_artifact_id = f"runs/{change.change_id}/payload_{change.payload_id}"

        if change.operation_kind == "create" and before_sha256 is None:
            pass

        return PatchPayload(
            payload_id=change.payload_id if change.payload_id else f"pl_{change.change_id}",
            change_id=change.change_id,
            payload_kind="full_after_content",
            before_sha256=before_sha256,
            target_before_sha256=change.target_before_sha256,
            target_path=change.repository_path,
            payload_artifact_id=payload_artifact_id,
            payload_sha256=payload_sha256,
        )

    @staticmethod
    def _generate_proposed_content(
        change: PlannedRepositoryChange,
        before_content: Optional[bytes],
    ) -> bytes:
        """Generate proposed file content.

        MVP: wraps existing content with a placeholder comment.
        Future: LLM-driven code synthesis.
        """
        lines = [
            f"# PatchMaterializer: {change.change_id}",
            f"# operation: {change.operation_kind}",
        ]
        if change.symbol_delta:
            lines.append(f"# Symbol: {change.symbol_delta.symbol_name}")
        trailer = "\n".join(lines).encode("utf-8") + b"\n"

        if before_content:
            return before_content.rstrip(b"\n") + b"\n" + trailer
        return trailer


def build_payload_manifest(
    *,
    run_id: str,
    workspace_id: str,
    payloads: list[PatchPayload],
    proposed_diff_artifact_id: str,
    manifest_id: Optional[str] = None,
) -> PatchPayloadManifest:
    """Build a PatchPayloadManifest from materialized payloads."""
    import hashlib
    import json

    placeholder_sha = hashlib.sha256(b"placeholder").hexdigest()
    manifest = PatchPayloadManifest(
        manifest_id=manifest_id or f"manifest_{run_id}_{workspace_id}",
        workspace_id=workspace_id,
        payloads=payloads,
        proposed_diff_artifact_id=proposed_diff_artifact_id,
        manifest_sha256=placeholder_sha,
    )
    canonical = json.dumps(
        manifest.model_dump(mode="python", exclude={"manifest_sha256"}),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    manifest.manifest_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return manifest
