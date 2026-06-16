"""Reader core services — PaperReader 与 RepositoryReader。

负责：
- 读取 source_manifest.json
- 查找 source_id 并校验 source kind
- 调用 backend
- 校验 backend 输出（run_id / source_id 一致性）
- 写入正式 artifact
- 返回 StageResult
"""

from pathlib import Path

from autoad_researcher.core.artifacts import ArtifactStore
from autoad_researcher.core.stage_result import StageResult
from autoad_researcher.readers.base import PaperReaderBackend, RepositoryReaderBackend
from autoad_researcher.schemas import SourceManifest


# ------------------------------------------------------------------
# 公共 source 查找
# ------------------------------------------------------------------


def _find_source(manifest: SourceManifest, source_id: str):
    for source in manifest.sources:
        if source.source_id == source_id:
            return source

    raise ValueError(f"source_id not found in source manifest: {source_id!r}")


# ------------------------------------------------------------------
# PaperReader
# ------------------------------------------------------------------


class PaperReader:
    """论文读取的 core service。"""

    def __init__(
        self,
        backend: PaperReaderBackend,
        runs_root: str | Path = "runs",
    ) -> None:
        self._backend = backend
        self._artifacts = ArtifactStore(runs_root=runs_root)

    def run(self, run_id: str, *, source_id: str) -> StageResult:
        manifest = self._artifacts.read_model(
            run_id, "source_manifest.json", SourceManifest
        )
        source = _find_source(manifest, source_id)

        if source.kind not in {"paper_pdf", "paper_text"}:
            raise ValueError(
                f"source {source_id!r} is not a paper source: {source.kind!r}"
            )

        summary = self._backend.read_paper(run_id=run_id, source=source)

        if summary.run_id != run_id:
            raise ValueError("paper summary run_id mismatch")
        if summary.source_id != source_id:
            raise ValueError("paper summary source_id mismatch")

        self._artifacts.write_json(run_id, "paper_summary.json", summary)

        return StageResult(
            run_id=run_id,
            stage="paper_reading",
            status="success",
            artifacts=["paper_summary.json"],
            metadata={
                "source_id": source_id,
                "backend": self._backend.__class__.__name__,
            },
        )


# ------------------------------------------------------------------
# RepositoryReader
# ------------------------------------------------------------------


class RepositoryReader:
    """代码仓库读取的 core service。"""

    def __init__(
        self,
        backend: RepositoryReaderBackend,
        runs_root: str | Path = "runs",
    ) -> None:
        self._backend = backend
        self._artifacts = ArtifactStore(runs_root=runs_root)

    def run(self, run_id: str, *, source_id: str) -> StageResult:
        manifest = self._artifacts.read_model(
            run_id, "source_manifest.json", SourceManifest
        )
        source = _find_source(manifest, source_id)

        if source.kind != "repository":
            raise ValueError(
                f"source {source_id!r} is not a repository source: {source.kind!r}"
            )

        summary = self._backend.read_repository(run_id=run_id, source=source)

        if summary.run_id != run_id:
            raise ValueError("repository summary run_id mismatch")
        if summary.source_id != source_id:
            raise ValueError("repository summary source_id mismatch")

        self._artifacts.write_json(run_id, "repo_summary.json", summary)

        return StageResult(
            run_id=run_id,
            stage="repository_reading",
            status="success",
            artifacts=["repo_summary.json"],
            metadata={
                "source_id": source_id,
                "backend": self._backend.__class__.__name__,
            },
        )
