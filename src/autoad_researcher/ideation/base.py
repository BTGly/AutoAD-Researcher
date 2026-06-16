"""Idea Generation backend 的框架无关接口。"""

from abc import ABC, abstractmethod

from autoad_researcher.schemas import IdeaContext, IdeaGenerationResult


class IdeaGenerationBackend(ABC):
    """框架无关的 Idea generation backend。"""

    @abstractmethod
    def generate_ideas(self, *, context: IdeaContext) -> IdeaGenerationResult:
        ...
