"""Intent Clarifier 的框架无关后端接口。"""

from abc import ABC, abstractmethod

from autoad_researcher.schemas import ClarificationContext, ClarifiedTask


class IntentClarifierBackend(ABC):
    """Intent Clarifier 的框架无关后端。"""

    @abstractmethod
    def clarify(self, *, context: ClarificationContext) -> ClarifiedTask:
        ...
