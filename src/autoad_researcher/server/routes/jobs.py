from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/runs", tags=["jobs"])


@router.get("/{run_id}/jobs")
async def get_jobs(run_id: str):
    path = Path("runs") / run_id / "ui_chat" / "material_subagent_runs.jsonl"
    if not path.is_file():
        return []
    import json
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return result
