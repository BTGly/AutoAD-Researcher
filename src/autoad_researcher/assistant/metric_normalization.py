"""Deterministic normalization of explicitly mentioned evaluation metrics."""

from __future__ import annotations

import re
from typing import Any


def canonicalize_metrics(value: Any) -> list[str]:
    """Map metric phrases to canonical metric ids without deciding intent."""

    if isinstance(value, list):
        raw_items = [str(item) for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        raw_items = [value]
    else:
        return []

    metrics: list[str] = []
    for item in raw_items:
        lowered = item.lower()
        item_metrics: list[str] = []
        if (
            re.search(
                r"(?<![A-Za-z0-9_])auroc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc-?roc(?![A-Za-z0-9_])",
                lowered,
            )
            and any(token in item for token in ("两种", "两个", "主流"))
        ):
            item_metrics.extend(["image_level_auroc", "pixel_level_auroc"])
        if re.search(
            r"image[-_\s]*(level[-_\s]*)?(auc|auroc)|instance[-_\s]*(level[-_\s]*)?(auc|auroc)",
            lowered,
        ):
            item_metrics.append("image_level_auroc")
        if re.search(
            r"pixel[-_\s]*(level[-_\s]*)?(auc|auroc)|full[-_\s]*pixel[-_\s]*(auc|auroc)|定位",
            lowered,
        ):
            item_metrics.append("pixel_level_auroc")
        if re.search(r"\bpro\b|per[-_\s]*region[-_\s]*overlap|\bau[-_\s]*pro\b", lowered):
            item_metrics.append("pro")
        if re.search(r"\bf1\b|f1[-_\s]*score", lowered):
            item_metrics.append("f1")
        if re.search(r"accuracy|准确率", lowered):
            item_metrics.append("accuracy")
        if re.search(r"速度|推理速度|latency|throughput|fps", lowered):
            item_metrics.append("inference_latency")
        if re.search(r"显存|memory|vram", lowered):
            item_metrics.append("peak_vram")
        if not item_metrics and re.search(
            r"(?<![A-Za-z0-9_])auroc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc-?roc(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])auc(?![A-Za-z0-9_])",
            lowered,
        ):
            item_metrics.append("image_level_auroc")
        if not item_metrics and item in {
            "image_level_auroc",
            "pixel_level_auroc",
            "pro",
            "f1",
            "accuracy",
            "inference_latency",
            "peak_vram",
        }:
            item_metrics.append(item)
        for metric in item_metrics:
            if metric not in metrics:
                metrics.append(metric)
    return metrics
