"""pytest 共享 fixtures。"""

import pytest


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
