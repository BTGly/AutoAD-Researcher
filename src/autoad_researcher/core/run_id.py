"""run_id 安全校验模块。

所有需要将 run_id 映射为 runs_root/{run_id} 路径的模块都应复用此模块，
避免在多处重复校验逻辑。
"""

import re
from pathlib import Path


# 只允许字母、数字、下划线、连字符、点号
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_run_id(runs_root: str | Path, run_id: str) -> None:
    """校验 run_id 安全性。

    拒绝：
    - 空字符串
    - 纯点号（., .., ...），会解析回 runs_root 本身
    - 路径穿越字符（/, \\, ..）
    - 不匹配 [A-Za-z0-9_.-]+ 的字符
    - 解析后路径逃逸 runs_root

    Raises:
        ValueError: run_id 不合法
    """
    if not run_id:
        raise ValueError("run_id must not be empty")
    if set(run_id) == {"."}:
        raise ValueError(
            f"run_id must not be dot-only (resolves to runs_root itself): {run_id!r}"
        )
    if ".." in run_id.split("/") or ".." in run_id.split("\\"):
        raise ValueError(f"run_id contains path traversal: {run_id!r}")
    if not _RUN_ID_PATTERN.match(run_id):
        raise ValueError(
            f"run_id must match {_RUN_ID_PATTERN.pattern}, got: {run_id!r}"
        )

    root = Path(runs_root)
    resolved_root = root.resolve()
    resolved_run = (root / run_id).resolve()

    try:
        resolved_run.relative_to(resolved_root)
    except ValueError:
        raise ValueError(
            f"run_id escapes runs_root: "
            f"runs_root={resolved_root}, resolved={resolved_run}"
        ) from None


def run_dir_path(runs_root: str | Path, run_id: str) -> Path:
    """返回 runs_root/run_id 路径（调用前先校验 run_id）。"""
    validate_run_id(runs_root, run_id)
    return Path(runs_root) / run_id
