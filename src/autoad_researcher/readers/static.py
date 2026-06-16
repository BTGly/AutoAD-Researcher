"""Static reader backends — 不调用 LLM，用于 deterministic 测试。"""

from autoad_researcher.readers.base import PaperReaderBackend, RepositoryReaderBackend
from autoad_researcher.schemas import PaperSummary, RepositorySummary, SourceEntry


class StaticPaperReaderBackend(PaperReaderBackend):
    """返回预置 PaperSummary 的 static backend。"""

    def __init__(self, summary: PaperSummary) -> None:
        self._summary = summary

    def read_paper(self, *, run_id: str, source: SourceEntry) -> PaperSummary:
        return self._summary


class StaticRepositoryReaderBackend(RepositoryReaderBackend):
    """返回预置 RepositorySummary 的 static backend。"""

    def __init__(self, summary: RepositorySummary) -> None:
        self._summary = summary

    def read_repository(self, *, run_id: str, source: SourceEntry) -> RepositorySummary:
        return self._summary
