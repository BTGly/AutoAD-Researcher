from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{artifact_path:path}")
async def get_artifact(artifact_path: str):
    full = Path("runs") / artifact_path
    if not full.exists():
        raise HTTPException(404, "artifact not found")
    if full.stat().st_size > 10 * 1024 * 1024:
        raise HTTPException(413, "artifact too large (>10MB)")
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        raise HTTPException(500, "cannot read artifact")
    return {"path": artifact_path, "content": content}
