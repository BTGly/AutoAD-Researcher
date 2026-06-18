"""Generic artifact reference for Step 3.7+ execution artifacts.

The early-stage ``ArtifactReference`` in ``clarification.py`` is restricted to a
fixed Literal of 3.0–3.4 filenames and has no ``sha256``.  Execution-stage
artifacts (patch manifests, validation reports, metrics, validity, resource
usage, reproducibility reports, etc.) need a generic, SHA-bearing reference.

``ArtifactReferenceV2`` is that generic contract.  It does **not** replace
``ArtifactReference``; 3.0–3.4 schemas continue to use the restricted Literal
form.  3.7+ schemas (``patch_planning.py`` v2 handoff, 3.8 execution records,
3.9 analysis reports) use ``ArtifactReferenceV2``.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.paper_intelligence.ids import Sha256Pattern


class ArtifactReferenceV2(BaseModel):
    """A SHA-addressable on-disk artifact produced by any pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    artifact_type: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    sha256: str = Field(pattern=Sha256Pattern)
    source_id: str | None = None
    size_bytes: int | None = None
