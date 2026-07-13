"""Domain-separated canonical hashes for control-plane identities."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from pydantic import BaseModel

from autoad_researcher.benchmarks.hashing import canonical_json_bytes


def domain_sha256(domain: str, value: BaseModel | Mapping[str, Any]) -> str:
    if not domain or "\x00" in domain:
        raise ValueError("hash domain must be a non-empty NUL-free string")
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + canonical_json_bytes(value)).hexdigest()


def pipeline_job_request_sha256(
    *,
    source_id: str,
    job_type: str,
    evidence_role: str,
    payload: Mapping[str, Any],
) -> str:
    return domain_sha256(
        "autoad:pipeline_job_request:v1",
        {
            "source_id": source_id,
            "job_type": job_type,
            "evidence_role": evidence_role,
            "payload": dict(payload),
        },
    )


def event_payload_sha256(*, event_type: str, payload: Mapping[str, Any]) -> str:
    return domain_sha256(
        "autoad:control_plane_event:v1",
        {"type": event_type, "payload": dict(payload)},
    )
