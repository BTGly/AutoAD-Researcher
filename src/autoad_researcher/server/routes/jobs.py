from fastapi import APIRouter

from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs", tags=["jobs"])


@router.get("/{run_id}/jobs")
async def get_jobs(run_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if not run_dir.exists():
        return []

    jobs = load_pipeline_jobs(run_dir)
    if jobs:
        return jobs

    # Fallback: legacy material_subagent_runs
    legacy_path = run_dir / "ui_chat" / "material_subagent_runs.jsonl"
    if legacy_path.is_file():
        import json
        result = []
        for line in legacy_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return result

    return []
