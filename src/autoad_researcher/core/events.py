"""EventStore — AutoAD run 事件日志。

以 JSONL 格式追加记录 runs/{run_id}/events.jsonl。
每行一个 JSON object，适合长任务追加，崩溃后不易损坏。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.core.run_id import run_dir_path

# 当前阶段支持的 event_type 白名单
ALLOWED_EVENT_TYPES = {
    "run_created",
    "stage_started",
    "stage_completed",
    "stage_failed",
    "artifact_written",
    "artifact_read",
}


class EventRecord(BaseModel):
    """单条 run event。"""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    run_id: str
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class EventStore:
    """管理 runs/{run_id}/events.jsonl。

    events.jsonl 不是普通 artifact — 只能由 EventStore 追加写入，
    不能通过 ArtifactStore.write_json() 覆盖。
    """

    def __init__(self, runs_root: str | Path = "runs") -> None:
        self._runs_root = Path(runs_root)

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def event_path(self, run_id: str) -> Path:
        """返回 runs/{run_id}/events.jsonl。"""
        return run_dir_path(self._runs_root, run_id) / "events.jsonl"

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def append(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        """追加一条事件到 events.jsonl。"""
        self._validate_event_type(event_type)

        path = self.event_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        event = EventRecord(
            event_type=event_type,
            run_id=run_id,
            timestamp=datetime.now(timezone.utc),
            payload=payload or {},
        )

        with path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json())
            f.write("\n")

        return event

    def record_run_created(
        self,
        run_id: str,
        payload: dict[str, Any] | None = None,
    ) -> EventRecord:
        return self.append(run_id, "run_created", payload=payload)

    def record_artifact_written(
        self,
        run_id: str,
        artifact: str,
        *,
        overwrite: bool,
    ) -> EventRecord:
        return self.append(
            run_id,
            "artifact_written",
            payload={"artifact": artifact, "overwrite": overwrite},
        )

    def record_artifact_read(
        self,
        run_id: str,
        artifact: str,
    ) -> EventRecord:
        return self.append(
            run_id,
            "artifact_read",
            payload={"artifact": artifact},
        )

    def record_stage_started(
        self,
        run_id: str,
        stage: str,
        *,
        backend: str,
    ) -> EventRecord:
        return self.append(
            run_id,
            "stage_started",
            payload={"stage": stage, "backend": backend},
        )

    def record_stage_completed(
        self,
        run_id: str,
        stage: str,
        *,
        backend: str,
        artifacts: list[str],
        status: str = "success",
    ) -> EventRecord:
        return self.append(
            run_id,
            "stage_completed",
            payload={
                "stage": stage,
                "backend": backend,
                "status": status,
                "artifacts": artifacts,
            },
        )

    def record_stage_failed(
        self,
        run_id: str,
        stage: str,
        *,
        backend: str,
        error_type: str,
        error_message: str,
    ) -> EventRecord:
        return self.append(
            run_id,
            "stage_failed",
            payload={
                "stage": stage,
                "backend": backend,
                "error_type": error_type,
                "error_message": error_message,
            },
        )

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def read_events(self, run_id: str) -> list[EventRecord]:
        """读取 events.jsonl。不存在则返回空列表。"""
        path = self.event_path(run_id)

        if not path.exists():
            return []

        events: list[EventRecord] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue

            try:
                data = json.loads(line)
                events.append(EventRecord.model_validate(data))
            except Exception as exc:
                raise ValueError(f"invalid event at {path}:{lineno}") from exc

        return events

    # ------------------------------------------------------------------
    # 内部校验
    # ------------------------------------------------------------------

    def _validate_event_type(self, event_type: str) -> None:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {event_type!r}")
