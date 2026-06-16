"""Internal benchmark config loader — 加载 YAML，Pydantic 校验，规范化 SHA256。"""

import hashlib
import json
from pathlib import Path

import yaml

from autoad_researcher.schemas import InternalBenchmarkCase


def load_internal_benchmark_case(path: str | Path) -> InternalBenchmarkCase:
    """从 YAML 文件加载并校验 InternalBenchmarkCase。"""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("benchmark config root must be a YAML mapping")
    return InternalBenchmarkCase.model_validate(data)


def canonical_case_json(case: InternalBenchmarkCase) -> bytes:
    """生成规范化 JSON，用于确定性 SHA256。"""
    return json.dumps(
        case.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_case_sha256(case: InternalBenchmarkCase) -> str:
    """返回规范化 JSON 的 SHA256 十六进制字符串。"""
    return hashlib.sha256(canonical_case_json(case)).hexdigest()
