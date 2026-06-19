"""
Shared types and the try_sources() dispatcher.

Each fetcher returns a DataPoint (or raises).  try_sources() calls them in
order and returns the first success, accumulating failure reasons.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Optional
from pipeline.schema import DataPoint


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_dp(
    value: float,
    source: str,
    source_detail: str,
    period_label: str = "",
    period_end: str = "",
) -> DataPoint:
    return DataPoint(
        value=value,
        source=source,
        source_detail=source_detail,
        period_label=period_label,
        period_end=period_end,
        fetched_at=now_iso(),
    )


def na_dp(na_reason: str) -> DataPoint:
    return DataPoint(na_reason=na_reason, fetched_at=now_iso())


def try_sources(
    field_name: str,
    fetchers: list[tuple[str, Callable[[], DataPoint]]],
) -> DataPoint:
    """
    Call each (label, fetcher_fn) in order.  Return the first DataPoint whose
    value is not None.  If all fail, return an na_dp with the combined reasons.
    """
    reasons: list[str] = []
    for label, fn in fetchers:
        try:
            dp = fn()
            if dp.value is not None:
                return dp
            reason = dp.na_reason or f"{label}: returned None"
            reasons.append(f"{label}: {reason}")
        except Exception as exc:
            reasons.append(f"{label}: exception — {exc}")
    return na_dp(f"All sources failed for {field_name}. " + " | ".join(reasons))
