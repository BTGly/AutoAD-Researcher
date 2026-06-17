"""Shared status literals for Repository Intelligence."""

from typing import Literal

ClaimStatus = Literal["confirmed", "inferred", "conflicting", "unknown"]
Confidence = Literal["low", "medium", "high"]
RepositoryIntelligenceStatus = Literal["success", "partial_success", "failed", "blocked"]
