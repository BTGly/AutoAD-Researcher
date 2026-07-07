from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

from autoad_researcher.server.config import RUNS_ROOT

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
async def list_runs():
    runs_dir = Path(RUNS_ROOT)
    if not runs_dir.exists():
        return []
    result = []
    for d in sorted(runs_dir.iterdir(), reverse=True):
        if d.is_dir():
            sources_path = d / "sources" / "source_references.json"
            count = 0
            if sources_path.is_file():
                import json
                try:
                    reg = json.loads(sources_path.read_text(encoding="utf-8"))
                    count = len(reg.get("sources", []))
                except Exception:
                    pass
            result.append({
                "run_id": d.name,
                "created_at": datetime.fromtimestamp(d.stat().st_ctime, tz=timezone.utc).isoformat(),
                "sources_count": count,
            })
    return result


@router.post("")
async def create_run():
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M_%f')}"
    run_dir = Path(RUNS_ROOT) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "sources").mkdir(exist_ok=True)
    (run_dir / "ui_chat").mkdir(exist_ok=True)
    (run_dir / "context").mkdir(exist_ok=True)
    return {"run_id": run_id}
