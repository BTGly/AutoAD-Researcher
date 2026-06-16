"""Ideation backends — Idea generation 的框架无关接口与 deterministic 实现。"""

from autoad_researcher.ideation.base import IdeaGenerationBackend
from autoad_researcher.ideation.direct import DirectIdeaBackend

__all__ = ["DirectIdeaBackend", "IdeaGenerationBackend"]
