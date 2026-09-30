"""Heatmap analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from anpr_common.geo import h3_to_str
from fastapi import APIRouter, HTTPException, Query, status

from api.deps import ClickHouseDep, UserDep

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _parse_window(window: str) -> timedelta:
    window = window.strip().lower()
    if window.endswith("m"):
        return timedelta(minutes=int(window[:-1]))
    if window.endswith("h"):
        return timedelta(hours=int(window[:-1]))
    if window.endswith("d"):
        return timedelta(days=int(window[:-1]))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid window format")


def _heatmap_cells(ch, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Prefer heatmap_1min; fall back to aggregating anpr_reads."""
    query = """
        SELECT h3_cell, sum(count) AS total
        FROM heatmap_1min
        WHERE minute >= {start:DateTime} AND minute <= {end:DateTime}
        GROUP BY h3_cell
        ORDER BY total DESC
    """
    result = ch.query(
        query,
        parameters={
            "start": start.replace(tzinfo=None) if start.tzinfo else start,
            "end": end.replace(tzinfo=None) if end.tzinfo else end,
        },
    )
    cells = [
        {"h3": h3_to_str(int(row[0])), "h3_int": int(row[0]), "count": int(row[1])}
        for row in result.result_rows
    ]
    if cells:
        return cells
    fallback = """
        SELECT h3_r8, count() AS total
        FROM anpr_reads
        WHERE ts >= {start:DateTime} AND ts <= {end:DateTime} AND h3_r8 != 0
        GROUP BY h3_r8
        ORDER BY total DESC
    """
    result = ch.query(
        fallback,
        parameters={
            "start": start.replace(tzinfo=None) if start.tzinfo else start,
            "end": end.replace(tzinfo=None) if end.tzinfo else end,
        },
    )
    return [
        {"h3": h3_to_str(int(row[0])), "h3_int": int(row[0]), "count": int(row[1])}
        for row in result.result_rows
    ]


@router.get("/heatmap")
async def heatmap(
    ch: ClickHouseDep,
    _user: UserDep,
    window: str = Query("15m"),
) -> dict[str, Any]:
    delta = _parse_window(window)
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    start = end - delta
    cells = _heatmap_cells(ch, start, end)
    stale = False
    latest_ts: str | None = None
    # If wall-clock window is empty (common under 60× sim), use latest data window.
    if not cells:
        latest = ch.query("SELECT max(ts) FROM anpr_reads")
        if latest.result_rows and latest.result_rows[0][0] is not None:
            end_raw = latest.result_rows[0][0]
            if hasattr(end_raw, "tzinfo") and end_raw.tzinfo is None:
                end_raw = end_raw.replace(tzinfo=UTC)
            latest_ts = end_raw.isoformat() if hasattr(end_raw, "isoformat") else str(end_raw)
            end = (
                end_raw.replace(second=0, microsecond=0) if hasattr(end_raw, "replace") else end_raw
            )
            start = end - delta
            cells = _heatmap_cells(ch, start, end)
            stale = True
    return {
        "window": window,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cells": cells,
        "stale": stale,
        "latest_ts": latest_ts,
    }
