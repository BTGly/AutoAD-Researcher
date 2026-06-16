"""AutoAD schema 定义。

所有阶段产物的 Pydantic 数据模型集中于此。
harness、pipeline、storage 等模块不应在各自目录内重复定义 schema。
"""

from autoad_researcher.schemas.experiment import ExperimentPlan
from autoad_researcher.schemas.intake import (
    InputTask,
    SourceEntry,
    SourceKind,
    SourceManifest,
)
from autoad_researcher.schemas.patch import PatchPlan

__all__ = [
    "ExperimentPlan",
    "InputTask",
    "PatchPlan",
    "SourceEntry",
    "SourceKind",
    "SourceManifest",
]
