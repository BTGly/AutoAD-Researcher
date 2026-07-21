from pathlib import Path

from fastapi import APIRouter

from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}", tags=["report"])

REPORT_PATHS = [
    "arbor_session/REPORT.md",
    "arbor_session/COORDINATOR_FINAL_REPORT.txt",
    "report.md",
    "REPORT.md",
]


def _find_report(run_dir: Path) -> str | None:
    for rel in REPORT_PATHS:
        path = run_dir / rel
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


@router.get("/report")
async def get_report(run_id: str):
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    content = _find_report(run_dir)
    if content is not None:
        return {"content": content}
    return {"content": ""}
