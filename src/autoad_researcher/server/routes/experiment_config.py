from pathlib import Path
import json

from fastapi import APIRouter

from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}", tags=["experiment-config"])

CONFIG_FILENAME = "experiment_config.json"


def _config_path(run_id: str) -> Path:
    return run_dir_or_400(RUNS_ROOT, run_id) / CONFIG_FILENAME


@router.get("/experiment-config")
async def get_experiment_config(run_id: str):
    path = _config_path(run_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


@router.put("/experiment-config")
async def save_experiment_config(run_id: str, config: dict):
    path = _config_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "run_id": run_id}
