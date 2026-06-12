"""SQLite 数据模型定义（占位）。"""

from pydantic import BaseModel


class ExperimentRecord(BaseModel):
    """单次实验记录。"""
    id: str
    paper_source: str
    transfer_judgment: dict | None = None
    experiment_plan: dict | None = None
    patch_path: str | None = None
    status: str = "pending"
