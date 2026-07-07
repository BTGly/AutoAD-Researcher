from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/runs", tags=["artifacts"])


@router.get("/{run_id}/artifacts/{artifact_path:path}")
async def get_artifact(run_id: str, artifact_path: str):
    run_dir = Path("runs") / run_id
    if not run_dir.exists():
        raise HTTPException(404, "run not found")

    full = (run_dir / artifact_path).resolve()
    if not str(full).startswith(str(run_dir.resolve())):
        raise HTTPException(403, "path traversal denied")

    if not full.exists():
        raise HTTPException(404, "artifact not found")
    if full.stat().st_size > 10 * 1024 * 1024:
        raise HTTPException(413, "artifact too large (>10MB)")
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except Exception:
        raise HTTPException(500, "cannot read artifact")
    return {"path": artifact_path, "run_id": run_id, "content": content}
