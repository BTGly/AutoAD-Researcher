"""User decision approval artifacts — for internal demo human-in-the-loop bypass."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Stage3Approval(BaseModel):
    """A recorded user decision that unblocks a pipeline stage.

    Written to ``runs/<run_id>/approvals/<decision_type>.json``.
    In a real deployment these would come from an interactive UI; for the
    internal demo they are placed ahead of time as JSON files.
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
