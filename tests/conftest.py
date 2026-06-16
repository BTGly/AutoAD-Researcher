"""pytest 共享 fixtures 和 helpers。"""

import pytest

from autoad_researcher.schemas import ClarifiedTask, ConfirmedDecision


def make_clarified_task(**kw):
    """构造合法 ClarifiedTask，自动为 baseline 添加 baseline_decision。"""
    if kw.get("baseline") and not kw.get("baseline_decision"):
        kw.setdefault("baseline_decision", ConfirmedDecision(
            value=kw["baseline"],
            source="user_provided",
            evidence="test:baseline",
        ))
    return ClarifiedTask(**kw)


@pytest.fixture
def sample_paper_summary():
    """返回一个示例论文结构化抽取结果。"""
    return {
        "task_type": "表征学习",
        "core_idea": "多尺度特征融合 + 对比学习",
        "model_components": ["backbone", "loss", "feature_fusion"],
        "requires_anomaly_labels": False,
        "datasets": ["MVTec-AD"],
        "metrics": ["image_auroc", "pixel_auroc"],
    }
