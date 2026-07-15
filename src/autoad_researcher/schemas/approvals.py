"""Human approval artifacts for HITL pipeline gates."""

from datetime import datetime, timezone
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SECRET_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def _reject_secret_like_payload(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if _SECRET_LIKE_RE.search(text):
        raise ValueError("approval artifacts must not contain API-key-like secrets")


class Stage3Approval(BaseModel):
    """A recorded user decision that unblocks a pipeline stage.

    Written to ``runs/<run_id>/approvals/<decision_type>.json``.
    In a real deployment these come from an interactive UI; for the internal
    demo they may also be placed ahead of time as JSON files.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    decision_type: str = Field(
        pattern=r"^(idea_confirmation|variant_selection|patch_approval|run_approval)$"
    )
    confirmed_by_user: bool
    user_idea_label: str | None = None
    selected_idea_source_id: str | None = None
    selected_variant_ids: list[str] = Field(default_factory=list)
    rejected_variant_ids: list[str] = Field(default_factory=list)
    user_confirmation_text: str | None = None
    created_at: datetime
    evidence_kind: str = Field(
        pattern=r"^(user_input|cli_flag|approval_artifact)$",
        default="approval_artifact",
    )

    @model_validator(mode="after")
    def _validate_no_secrets(self) -> "Stage3Approval":
        _reject_secret_like_payload(self.model_dump(mode="json"))
        return self
