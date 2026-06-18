"""Experiment planner module — Step 3.5 Multi-variant Experiment Planner.

Business-logic builders, validators, and emitter live here.
All Pydantic models live in ``schemas/experiment_planning.py`` (repo convention).
"""

from autoad_researcher.experiment.adapter_34 import (
    Stage34HandoffError,
    Stage34InputAdapter,
    compute_unresolved_dimension_id,
    derive_preparation_phase,
)

__all__ = [
    "Stage34HandoffError",
    "Stage34InputAdapter",
    "compute_unresolved_dimension_id",
    "derive_preparation_phase",
]
