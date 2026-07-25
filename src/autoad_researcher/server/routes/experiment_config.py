from pathlib import Path
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from autoad_researcher.experiment.preparation import (
    ExperimentPreparation,
    PreparationStore,
    empty_preparation,
)
from autoad_researcher.experiment.preparation_coordinator import (
    ExperimentPreparationCoordinator,
    PreparationInvestigationRequest,
    PreparationInvestigationResult,
)
from autoad_researcher.experiment.public_weights import resolve_clip_weight
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.run_paths import run_dir_or_400

router = APIRouter(prefix="/api/runs/{run_id}", tags=["experiment-config"])

CONFIG_FILENAME = "experiment_config.json"


class ResolvePreparationAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    user_path: str | None = None
    auto_download: bool = False


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


@router.get("/experiment/preparation", response_model=ExperimentPreparation)
async def get_experiment_preparation(run_id: str) -> ExperimentPreparation:
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    preparation = PreparationStore().load(run_dir)
    return preparation or empty_preparation(run_id)


@router.put("/experiment/preparation", response_model=ExperimentPreparation)
async def save_experiment_preparation(run_id: str, preparation: ExperimentPreparation) -> ExperimentPreparation:
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if preparation.run_id != run_id:
        raise HTTPException(status_code=409, detail="preparation run_id does not match URL")
    return PreparationStore().save(run_dir, preparation)


@router.post("/experiment/preparation/investigate", response_model=PreparationInvestigationResult)
async def investigate_experiment_preparation(run_id: str, request: PreparationInvestigationRequest) -> PreparationInvestigationResult:
    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    if request.repositories and any(item.path and ".." in Path(item.path).parts for item in request.repositories):
        raise ValueError("repository paths must not contain parent traversal")
    return ExperimentPreparationCoordinator().investigate(run_dir, request)


@router.post("/experiment/preparation/assets/resolve", response_model=ExperimentPreparation)
async def resolve_preparation_asset(run_id: str, request: ResolvePreparationAssetRequest) -> ExperimentPreparation:
    """Verify a public model weight or a user-provided replacement path."""

    run_dir = run_dir_or_400(RUNS_ROOT, run_id)
    preparation = PreparationStore().load(run_dir) or empty_preparation(run_id)
    asset = next((item for item in preparation.assets if item.asset_id == request.asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="preparation asset not found")
    if asset.kind != "model_weight" or request.asset_id != "clip_vit_l_14_336px":
        raise HTTPException(status_code=409, detail="asset does not have a registered public resolver")
    resolution = resolve_clip_weight(
        asset_id=request.asset_id,
        user_path=request.user_path,
        auto_download=request.auto_download,
    )
    updated_asset = asset.model_copy(update={
        "status": "verified" if resolution.status == "available" else resolution.status,
        "path": resolution.path or asset.path,
        "source": resolution.source,
        "sha256": resolution.actual_sha256,
    })
    updated = preparation.model_copy(update={
        "assets": [updated_asset if item.asset_id == asset.asset_id else item for item in preparation.assets],
        "evidence": [*preparation.evidence, {
            "evidence_id": f"weight_{asset.asset_id}_{len(preparation.evidence):06d}",
            "kind": "verified" if resolution.status == "available" else "observed",
            "summary": resolution.error or "public weight verified",
            "file_path": resolution.path,
            "output_sha256": resolution.actual_sha256,
        }],
    })
    updated = ExperimentPreparationCoordinator._add_asset_actions(updated)
    return PreparationStore().save(run_dir, updated)
