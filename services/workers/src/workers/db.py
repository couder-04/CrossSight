"""Database client helpers for workers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
import clickhouse_connect
import redis.asyncio as aioredis
from anpr_common.config import Settings, get_settings


@dataclass(frozen=True)
class CameraInfo:
    id: str
    lat: float
    lng: float
    heading_deg: float
    allowed_direction: str | None


@dataclass(frozen=True)
class CameraPair:
    camera_a: str
    camera_b: str
    distance_m: float
    adjacent: bool


@dataclass(frozen=True)
class ZoneInfo:
    id: str
    name: str
    kind: str
    active_hours: dict[str, Any] | None
    # WKT or GeoJSON for point-in-polygon tests via PostGIS in rules


class ClickHouseClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = clickhouse_connect.get_client(
            host=self.settings.clickhouse_host,
            port=self.settings.clickhouse_port,
            username=self.settings.clickhouse_user,
            password=self.settings.clickhouse_password or "",
            database=self.settings.clickhouse_db,
        )

    def insert_reads(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = [
            "event_id",
            "camera_id",
            "ts",
            "plate_raw",
            "plate_norm",
            "plate_valid",
            "plate_format",
            "confidence",
            "char_conf",
            "alternates",
            "lane",
            "direction",
            "vehicle_class",
            "color",
            "make",
            "speed_kmh",
            "crop_key",
            "source",
            "h3_r8",
            "h3_r7",
        ]
        data = [[row[c] for c in columns] for row in rows]
        self._client.insert("anpr_reads", data, column_names=columns)

    def insert_flow_5min(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = [
            "camera_id",
            "lane",
            "window_start",
            "counts_car",
            "counts_motorcycle",
            "counts_bus",
            "counts_truck",
            "counts_auto",
            "counts_other",
            "avg_speed_kmh",
            "volume",
        ]
        data = [[row.get(c) for c in columns] for row in rows]
        self._client.insert("flow_5min", data, column_names=columns)

    def insert_segment_speed(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = [
            "camera_a",
            "camera_b",
            "window_start",
            "median_travel_s",
            "median_speed_kmh",
            "sample_count",
        ]
        data = [[row[c] for c in columns] for row in rows]
        self._client.insert("segment_speed_5min", data, column_names=columns)

    def insert_od_hourly(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = ["origin_h3", "dest_h3", "hour", "trip_count"]
        data = [[row[c] for c in columns] for row in rows]
        self._client.insert("od_hourly", data, column_names=columns)

    def insert_heatmap_1min(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = ["h3_cell", "minute", "count"]
        data = [[row[c] for c in columns] for row in rows]
        self._client.insert("heatmap_1min", data, column_names=columns)

    def query_free_flow_speeds(self) -> dict[tuple[str, str], float]:
        """85th percentile segment speed during 00:00-05:00 (free-flow baseline)."""
        sql = """
        SELECT camera_a, camera_b,
               quantile(0.85)(median_speed_kmh) AS free_flow
        FROM segment_speed_5min
        WHERE toHour(window_start) < 5
        GROUP BY camera_a, camera_b
        """
        result = self._client.query(sql)
        return {(r[0], r[1]): float(r[2]) for r in result.result_rows}

    def query_camera_volume_baselines(self) -> dict[tuple[str, int], tuple[float, float]]:
        """Hour-of-week (camera, dow*24+hour) -> (mean, std) volume from flow_5min."""
        sql = """
        SELECT camera_id,
               toDayOfWeek(window_start) * 24 + toHour(window_start) AS how,
               avg(volume) AS mu,
               stddevPop(volume) AS sigma
        FROM flow_5min
        GROUP BY camera_id, how
        HAVING sigma > 0
        """
        result = self._client.query(sql)
        out: dict[tuple[str, int], tuple[float, float]] = {}
        for camera_id, how, mu, sigma in result.result_rows:
            out[(camera_id, int(how))] = (float(mu), float(sigma))
        return out

    async def insert_reads_async(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.insert_reads, rows)

    async def insert_flow_5min_async(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.insert_flow_5min, rows)

    async def insert_segment_speed_async(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.insert_segment_speed, rows)

    async def insert_od_hourly_async(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.insert_od_hourly, rows)

    async def insert_heatmap_1min_async(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.to_thread(self.insert_heatmap_1min, rows)


async def create_redis(settings: Settings | None = None) -> aioredis.Redis:
    settings = settings or get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def create_pg_pool(settings: Settings | None = None) -> asyncpg.Pool:
    settings = settings or get_settings()
    return await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        min_size=1,
        max_size=5,
    )


async def load_cameras(pool: asyncpg.Pool) -> dict[str, CameraInfo]:
    rows = await pool.fetch(
        """
        SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lng,
               heading_deg, allowed_direction
        FROM cameras
        """
    )
    return {
        r["id"]: CameraInfo(
            id=r["id"],
            lat=float(r["lat"]),
            lng=float(r["lng"]),
            heading_deg=float(r["heading_deg"]),
            allowed_direction=r["allowed_direction"],
        )
        for r in rows
    }


async def load_camera_pairs(pool: asyncpg.Pool, adjacent_only: bool = True) -> list[CameraPair]:
    where = "WHERE adjacent = TRUE" if adjacent_only else ""
    rows = await pool.fetch(
        f"""
        SELECT camera_a, camera_b, distance_m, adjacent
        FROM camera_pairs
        {where}
        """
    )
    return [
        CameraPair(
            camera_a=r["camera_a"],
            camera_b=r["camera_b"],
            distance_m=float(r["distance_m"]),
            adjacent=bool(r["adjacent"]),
        )
        for r in rows
    ]


async def load_watchlist(pool: asyncpg.Pool) -> set[str]:
    rows = await pool.fetch(
        """
        SELECT plate_norm FROM watchlist
        WHERE expires_at IS NULL OR expires_at > now()
        """
    )
    return {r["plate_norm"] for r in rows}


async def persist_alert(pool: asyncpg.Pool, alert: dict[str, Any]) -> None:
    import json

    await pool.execute(
        """
        INSERT INTO alerts (
            id, type, severity, plate_norm, camera_ids, evidence,
            status, needs_verification, created_at, updated_at
        ) VALUES (
            $1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $9
        )
        ON CONFLICT (id) DO NOTHING
        """,
        str(alert["id"]),
        alert["type"],
        alert["severity"],
        alert["plate_norm"],
        alert.get("camera_ids", []),
        json.dumps(alert.get("evidence", {})),
        alert.get("status", "new"),
        alert.get("needs_verification", False),
        alert.get("ts", datetime.now(UTC)),
    )


async def camera_in_zone(pool: asyncpg.Pool, camera_id: str, zone_kind: str) -> str | None:
    # Sensitive zones: treat a 300m buffer as "inside" for loitering (spec: buffered zone).
    if zone_kind == "sensitive":
        row = await pool.fetchrow(
            """
            SELECT z.id
            FROM zones z
            JOIN cameras c ON c.id = $1
            WHERE z.kind = 'sensitive'
              AND ST_DWithin(z.geom::geography, c.geom::geography, 300)
            LIMIT 1
            """,
            camera_id,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT z.id
            FROM zones z
            JOIN cameras c ON c.id = $1
            WHERE z.kind = $2
              AND ST_Contains(z.geom, c.geom)
            LIMIT 1
            """,
            camera_id,
            zone_kind,
        )
    return row["id"] if row else None


async def camera_in_restricted_zone(pool: asyncpg.Pool, camera_id: str) -> tuple[str, dict] | None:
    row = await pool.fetchrow(
        """
        SELECT z.id, z.active_hours
        FROM zones z
        JOIN cameras c ON c.id = $1
        WHERE z.kind = 'restricted'
          AND ST_Contains(z.geom, c.geom)
        LIMIT 1
        """,
        camera_id,
    )
    if not row:
        return None
    return row["id"], row["active_hours"] or {}
