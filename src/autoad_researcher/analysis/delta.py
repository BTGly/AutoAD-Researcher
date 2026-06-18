"""Step 3.9: Delta computation — sealed implementation."""

from typing import Literal

_EPSILON = 1e-10


def compute_deltas(
    baseline: float,
    variant: float,
    direction: Literal["maximize", "minimize"],
) -> tuple[float, float, float | None, float | None]:
    """Compute raw delta, improvement delta, and relative percentages.

    Returns (raw_delta, improvement_delta, raw_relative_pct, improvement_relative_pct).
    """
    raw_delta = variant - baseline
    if direction == "maximize":
        improvement_delta = raw_delta
    else:
        improvement_delta = baseline - variant

    abs_baseline = abs(baseline)
    if abs_baseline < _EPSILON:
        raw_relative_pct = None
        improvement_relative_pct = None
    else:
        raw_relative_pct = raw_delta / abs_baseline * 100.0
        improvement_relative_pct = improvement_delta / abs_baseline * 100.0

    return raw_delta, improvement_delta, raw_relative_pct, improvement_relative_pct
