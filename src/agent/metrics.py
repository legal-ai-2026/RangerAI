from __future__ import annotations

from typing import Literal


def readiness_score(go_count: int, nogo_count: int, uncertain_count: int) -> float:
    raw = 70 + (go_count * 10) - (nogo_count * 15) - (uncertain_count * 5)
    return float(max(0, min(100, raw)))


def metric_status(value: float) -> Literal["strong", "watch", "critical"]:
    if value >= 75:
        return "strong"
    if value >= 50:
        return "watch"
    return "critical"
