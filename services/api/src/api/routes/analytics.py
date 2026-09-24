"""Traffic analytics endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from anpr_common.geo import h3_to_str
from fastapi import APIRouter, HTTPException, Query, status

from api.deps import ClickHouseDep, SettingsDep, UserDep

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
    # If wall-clock window is empty (common under 60× sim), use latest data window.
    if not cells:
        latest = ch.query("SELECT max(ts) FROM anpr_reads")
        if latest.result_rows and latest.result_rows[0][0] is not None:
            end_raw = latest.result_rows[0][0]
            if hasattr(end_raw, "tzinfo") and end_raw.tzinfo is None:
                end = end_raw.replace(tzinfo=UTC)
            else:
                end = end_raw if getattr(end_raw, "tzinfo", None) else datetime.now(UTC)
            end = end.replace(second=0, microsecond=0)
            start = end - delta
            cells = _heatmap_cells(ch, start, end)
    return {"window": window, "start": start.isoformat(), "end": end.isoformat(), "cells": cells}


@router.get("/flow")
async def flow(
    ch: ClickHouseDep,
    _user: UserDep,
    camera_id: str = Query(...),
    from_ts: datetime = Query(..., alias="from"),
    to_ts: datetime = Query(..., alias="to"),
) -> dict[str, Any]:
    query = """
        SELECT
            window_start,
            lane,
            counts_car, counts_motorcycle, counts_bus, counts_truck, counts_auto, counts_other,
            avg_speed_kmh,
            volume
        FROM flow_5min
        WHERE camera_id = {camera_id:String}
          AND window_start >= {start:DateTime}
          AND window_start <= {end:DateTime}
        ORDER BY window_start, lane
    """
    result = ch.query(
        query,
        parameters={"camera_id": camera_id, "start": from_ts, "end": to_ts},
    )
    rows = []
    for row in result.result_rows:
        rows.append(
            {
                "window_start": row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]),
                "lane": row[1],
                "counts_by_class": {
                    "car": int(row[2]),
                    "motorcycle": int(row[3]),
                    "bus": int(row[4]),
                    "truck": int(row[5]),
                    "auto": int(row[6]),
                    "other": int(row[7]),
                },
                "avg_speed_kmh": float(row[8]) if row[8] is not None else None,
                "volume": int(row[9]),
            }
        )
    return {"camera_id": camera_id, "from": from_ts.isoformat(), "to": to_ts.isoformat(), "windows": rows}


@router.get("/segments")
async def segments(
    ch: ClickHouseDep,
    _user: UserDep,
    at: datetime = Query(...),
) -> dict[str, Any]:
    """Congestion index per directed camera pair at the given time."""
    segments_out = _segment_congestion(ch, at)
    if not segments_out:
        latest = ch.query("SELECT max(window_start) FROM segment_speed_5min")
        if latest.result_rows and latest.result_rows[0][0] is not None:
            at = latest.result_rows[0][0]
            if hasattr(at, "tzinfo") and at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            segments_out = _segment_congestion(ch, at)
    return {"at": at.isoformat() if hasattr(at, "isoformat") else str(at), "segments": segments_out}


def _segment_congestion(ch, at: datetime) -> list[dict[str, Any]]:
    at_hour = at.replace(minute=0, second=0, microsecond=0)
    query = """
        SELECT
            camera_a,
            camera_b,
            median(median_speed_kmh) AS speed_kmh,
            median(median_travel_s) AS travel_s,
            sum(sample_count) AS samples
        FROM segment_speed_5min
        WHERE window_start >= {start:DateTime} - INTERVAL 1 HOUR
          AND window_start <= {start:DateTime} + INTERVAL 1 HOUR
        GROUP BY camera_a, camera_b
        HAVING samples > 0
    """
    current = ch.query(query, parameters={"start": at_hour})
    baseline_query = """
        SELECT camera_a, camera_b, quantile(0.85)(median_speed_kmh) AS free_flow
        FROM segment_speed_5min
        WHERE toHour(window_start) BETWEEN 0 AND 4
        GROUP BY camera_a, camera_b
    """
    try:
        baseline = ch.query(baseline_query)
        free_flow = {(r[0], r[1]): float(r[2]) for r in baseline.result_rows if r[2]}
    except Exception:
        free_flow = {}

    segments_out = []
    for row in current.result_rows:
        key = (row[0], row[1])
        speed = float(row[2]) if row[2] else 0.0
        ff = free_flow.get(key, speed if speed > 0 else 1.0)
        congestion = 1.0 - (speed / ff) if ff > 0 else 0.0
        segments_out.append(
            {
                "camera_a": row[0],
                "camera_b": row[1],
                "median_speed_kmh": speed,
                "median_travel_s": float(row[3]) if row[3] else None,
                "sample_count": int(row[4]),
                "free_flow_speed_kmh": ff,
                "congestion_index": max(0.0, min(1.0, congestion)),
            }
        )
    return segments_out


@router.get("/od")
async def od_matrix(
    ch: ClickHouseDep,
    settings: SettingsDep,
    _user: UserDep,
    hour: int = Query(..., ge=0, le=23),
    date: str = Query(..., description="YYYY-MM-DD"),
) -> dict[str, Any]:
    try:
        day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date") from exc
    hour_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0)
    hour_naive = hour_dt.replace(tzinfo=None)
    query = """
        SELECT origin_h3, dest_h3, sum(trip_count) AS trips
        FROM od_hourly
        WHERE toStartOfHour(hour) = {hour:DateTime}
        GROUP BY origin_h3, dest_h3
        HAVING trips >= {k:UInt32}
    """
    result = ch.query(query, parameters={"hour": hour_naive, "k": settings.od_k_anon})
    cells = [
        {
            "origin_h3": h3_to_str(int(row[0])),
            "dest_h3": h3_to_str(int(row[1])),
            "trip_count": int(row[2]),
        }
        for row in result.result_rows
    ]
    # Fallback: derive OD from raw reads when od_hourly is empty.
    if not cells:
        fallback = """
            SELECT origin, dest, count() AS trips
            FROM (
                SELECT
                    plate_norm,
                    argMin(h3_r7, ts) AS origin,
                    argMax(h3_r7, ts) AS dest
                FROM anpr_reads
                WHERE ts >= {start:DateTime}
                  AND ts < {end:DateTime}
                  AND h3_r7 != 0
                GROUP BY plate_norm
                HAVING origin != dest
            )
            GROUP BY origin, dest
            HAVING trips >= {k:UInt32}
        """
        end = hour_naive + timedelta(hours=1)
        result = ch.query(
            fallback,
            parameters={"start": hour_naive, "end": end, "k": settings.od_k_anon},
        )
        cells = [
            {
                "origin_h3": h3_to_str(int(row[0])),
                "dest_h3": h3_to_str(int(row[1])),
                "trip_count": int(row[2]),
            }
            for row in result.result_rows
        ]
    # Day-wide fallback for sparse synthetic demos (still k-anonymous).
    if not cells:
        day_start = hour_naive.replace(hour=0)
        day_end = day_start + timedelta(days=1)
        day_q = """
            SELECT origin_h3, dest_h3, sum(trip_count) AS trips
            FROM od_hourly
            WHERE hour >= {start:DateTime} AND hour < {end:DateTime}
            GROUP BY origin_h3, dest_h3
            HAVING trips >= {k:UInt32}
        """
        result = ch.query(
            day_q,
            parameters={"start": day_start, "end": day_end, "k": settings.od_k_anon},
        )
        cells = [
            {
                "origin_h3": h3_to_str(int(row[0])),
                "dest_h3": h3_to_str(int(row[1])),
                "trip_count": int(row[2]),
            }
            for row in result.result_rows
        ]
    if not cells:
        day_start = hour_naive.replace(hour=0)
        day_end = day_start + timedelta(days=1)
        day_reads = """
            SELECT origin, dest, count() AS trips
            FROM (
                SELECT
                    plate_norm,
                    argMin(h3_r7, ts) AS origin,
                    argMax(h3_r7, ts) AS dest
                FROM anpr_reads
                WHERE ts >= {start:DateTime}
                  AND ts < {end:DateTime}
                  AND h3_r7 != 0
                GROUP BY plate_norm
                HAVING origin != dest
            )
            GROUP BY origin, dest
            HAVING trips >= {k:UInt32}
        """
        result = ch.query(
            day_reads,
            parameters={"start": day_start, "end": day_end, "k": settings.od_k_anon},
        )
        cells = [
            {
                "origin_h3": h3_to_str(int(row[0])),
                "dest_h3": h3_to_str(int(row[1])),
                "trip_count": int(row[2]),
            }
            for row in result.result_rows
        ]
    return {
        "date": date,
        "hour": hour,
        "k_anonymity": settings.od_k_anon,
        "cells": cells,
    }


@router.get("/bottlenecks")
async def bottlenecks(ch: ClickHouseDep, _user: UserDep) -> dict[str, Any]:
    at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    seg_data = _segment_congestion(ch, at)
    flow_baselines = _camera_flow_baselines(ch)
    out = []
    for seg in seg_data:
        if seg["congestion_index"] < 0.5:
            continue
        downstream = seg["camera_b"]
        baseline = flow_baselines.get(downstream, 0)
        current = _current_camera_volume(ch, downstream, at)
        if baseline <= 0:
            continue
        if current >= 0.6 * baseline:
            continue
        out.append(
            {
                **seg,
                "downstream_camera": downstream,
                "current_volume": current,
                "baseline_volume": baseline,
            }
        )
    return {"at": at.isoformat(), "bottlenecks": out}


@router.get("/anomalies")
async def anomalies(ch: ClickHouseDep, _user: UserDep) -> dict[str, Any]:
    at = datetime.now(UTC)
    query = """
        SELECT camera_id, sum(volume) AS vol
        FROM flow_5min
        WHERE window_start >= {start:DateTime} - INTERVAL 15 MINUTE
        GROUP BY camera_id
    """
    current = {
        row[0]: int(row[1])
        for row in ch.query(query, parameters={"start": at}).result_rows
    }
    baseline_query = """
        SELECT
            camera_id,
            toDayOfWeek(window_start) AS dow,
            toHour(window_start) AS hr,
            avg(volume) AS avg_vol,
            stddevPop(volume) AS std_vol
        FROM flow_5min
        GROUP BY camera_id, dow, hr
    """
    baselines: dict[str, tuple[float, float]] = {}
    dow = at.isoweekday()
    hr = at.hour
    for row in ch.query(baseline_query).result_rows:
        if int(row[1]) == dow and int(row[2]) == hr:
            baselines[row[0]] = (float(row[3] or 0), float(row[4] or 1.0))

    flagged = []
    for cam_id, vol in current.items():
        avg_vol, std_vol = baselines.get(cam_id, (0.0, 1.0))
        if std_vol <= 0:
            continue
        z = (vol - avg_vol) / std_vol
        if z > 3:
            flagged.append(
                {
                    "camera_id": cam_id,
                    "volume": vol,
                    "baseline_avg": avg_vol,
                    "z_score": round(z, 3),
                }
            )
    flagged.sort(key=lambda x: x["z_score"], reverse=True)
    return {"at": at.isoformat(), "anomalies": flagged}


def _camera_flow_baselines(ch) -> dict[str, float]:
    query = """
        SELECT camera_id, avg(volume) AS baseline
        FROM flow_5min
        WHERE toHour(window_start) BETWEEN 0 AND 5
        GROUP BY camera_id
    """
    try:
        return {row[0]: float(row[1]) for row in ch.query(query).result_rows}
    except Exception:
        return {}


def _current_camera_volume(ch, camera_id: str, at: datetime) -> int:
    query = """
        SELECT sum(volume) FROM flow_5min
        WHERE camera_id = {cam:String}
          AND window_start >= {start:DateTime} - INTERVAL 15 MINUTE
    """
    result = ch.query(query, parameters={"cam": camera_id, "start": at})
    if not result.result_rows:
        return 0
    return int(result.result_rows[0][0] or 0)
