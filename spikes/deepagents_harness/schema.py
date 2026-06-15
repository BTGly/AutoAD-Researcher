"""Compatibility shim for Spike 01.

正式 schema 已迁移到 autoad_researcher.schemas。
保留此文件是为了让 spikes/deepagents_harness/run_spike.py
在不改入口的情况下继续可运行。
"""

from autoad_researcher.schemas import ExperimentPlan, PatchPlan  # noqa: F401

__all__ = [
    "ExperimentPlan",
    "PatchPlan",
]
