from pathlib import Path

from fastapi import APIRouter, HTTPException

from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs", tags=["artifacts"])


@router.get("/{run_id}/artifacts/{artifact_path:path}")
async def get_artifact(run_id: str, artifact_path: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if not run_dir.exists():
        raise HTTPException(404, "run not found")

    full = (run_dir / artifact_path).resolve()
    try:
        full.relative_to(run_dir.resolve())
    except ValueError:
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
