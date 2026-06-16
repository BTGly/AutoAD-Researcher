"""Reader backends — 论文和仓库读取的框架无关接口与 static 实现。"""

from autoad_researcher.readers.base import PaperReaderBackend, RepositoryReaderBackend
from autoad_researcher.readers.static import (
    StaticPaperReaderBackend,
    StaticRepositoryReaderBackend,
)

__all__ = [
    "PaperReaderBackend",
    "RepositoryReaderBackend",
    "StaticPaperReaderBackend",
    "StaticRepositoryReaderBackend",
]
