"""Reader backend 抽象接口。"""

from abc import ABC, abstractmethod

from autoad_researcher.schemas import PaperSummary, RepositorySummary, SourceEntry


class PaperReaderBackend(ABC):
    """论文读取后端的框架无关接口。"""

    @abstractmethod
    def read_paper(self, *, run_id: str, source: SourceEntry) -> PaperSummary:
        ...


class RepositoryReaderBackend(ABC):
    """代码仓库读取后端的框架无关接口。"""

    @abstractmethod
    def read_repository(self, *, run_id: str, source: SourceEntry) -> RepositorySummary:
        ...
