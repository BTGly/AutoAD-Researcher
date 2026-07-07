from pathlib import Path

from fastapi import APIRouter

from autoad_researcher.server.config import RUNS_ROOT

router = APIRouter(prefix="/api/runs", tags=["sources"])


@router.get("/{run_id}/sources")
async def get_sources(run_id: str):
    path = Path(RUNS_ROOT) / run_id / "sources" / "source_references.json"
    if not path.is_file():
        return []
    import json
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
        return reg.get("sources", [])
    except Exception:
        return []
