"""Non-sensitive model configuration audit metadata."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.server.config import RUNS_ROOT

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dialogue_model: str = Field(default="", max_length=128)
    report_model: str = Field(default="", max_length=128)
    experiment_model: str = Field(default="", max_length=128)
    provider_origin: str = Field(default="", max_length=256)
    has_api_key: bool = False
    schema_version: int = Field(default=1, ge=1, le=10)


@router.post("/audit", status_code=204)
async def record_config_audit(request: ConfigAuditRequest):
    parsed = urlsplit(request.provider_origin.strip())
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    payload = {
        "schema_version": request.schema_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dialogue_model": request.dialogue_model.strip(),
        "report_model": request.report_model.strip(),
        "experiment_model": request.experiment_model.strip(),
        "provider_origin": origin,
        "provider_host": parsed.netloc if origin else "",
        "has_api_key": request.has_api_key,
    }
    path = Path(RUNS_ROOT) / "config_audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # Audit metadata is best effort and must not block a usable config.
        pass
