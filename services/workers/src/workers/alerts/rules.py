"""Alert rule implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Protocol

from anpr_common.fuzzy import candidates
from anpr_common.geo import haversine_m, opposite_direction
from anpr_common.schemas import (
    Alert,
    AlertSeverity,
    AlertType,
    Direction,
    PlateRead,
)

from workers.alerts.registry import RegistryClient, RegistryRecord
from workers.db import CameraInfo, CameraPair

ALERT_DEDUP_MINUTES = 10
CONVOY_TIME_WINDOW_SEC = 20
CONVOY_MIN_COMMON_CAMERAS = 3
CONVOY_LOOKBACK_MIN = 30
LOITERING_MIN_SIGHTINGS = 5
LOITERING_WINDOW_MIN = 60


class RuleContext(Protocol):
    settings: Any
    cameras: dict[str, CameraInfo]
    watchlist: set[str]
    pair_distances: dict[tuple[str, str], float]
    redis: Any
    pg_pool: Any

    async def last_seen(self, plate_norm: str) -> tuple[str, datetime, float] | None: ...
    async def convoy_peers(self, plate_norm: str, camera_id: str, ts: datetime) -> set[str]: ...
    async def sensitive_zone_id(self, camera_id: str) -> str | None: ...
    async def restricted_zone(self, camera_id: str) -> tuple[str, dict] | None: ...
    async def plate_sightings_in_zone(
        self, plate_norm: str, zone_id: str, since: datetime
    ) -> int: ...


class Rule(ABC):
    name: str

    @abstractmethod
    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]: ...


def _read_evidence(read: PlateRead) -> dict[str, Any]:
    return {
        "event_id": str(read.event_id),
        "camera_id": read.camera_id,
        "ts": read.ts.isoformat(),
        "plate_norm": read.plate_norm,
        "confidence": read.confidence,
        "direction": read.direction.value if read.direction else None,
        "vehicle_class": read.vehicle_class.value,
    }


class WatchlistRule(Rule):
    name = "watchlist"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        plate = read.plate_norm.upper()
        if plate in ctx.watchlist:
            return [
                Alert(
                    type=AlertType.watchlist,
                    severity=AlertSeverity.high,
                    plate_norm=plate,
                    camera_ids=[read.camera_id],
                    evidence={"reads": [_read_evidence(read)], "match": "exact"},
                    ts=read.ts,
                )
            ]
        fuzzy_hits = candidates(plate, pool=list(ctx.watchlist), max_cost=1.0)
        if not fuzzy_hits:
            return []
        match_plate, cost = fuzzy_hits[0]
        return [
            Alert(
                type=AlertType.watchlist,
                severity=AlertSeverity.medium,
                plate_norm=plate,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "match": "fuzzy",
                    "watchlist_plate": match_plate,
                    "edit_cost": cost,
                },
                needs_verification=True,
                ts=read.ts,
            )
        ]


class ClonedPlateRule(Rule):
    name = "cloned_plate"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        if read.confidence < 0.8:
            return []
        last = await ctx.last_seen(read.plate_norm)
        if last is None:
            return []
        prev_cam_id, prev_ts, prev_conf = last
        if prev_conf < 0.8:
            return []
        if prev_cam_id == read.camera_id:
            return []
        prev_cam = ctx.cameras.get(prev_cam_id)
        cur_cam = ctx.cameras.get(read.camera_id)
        if prev_cam is None or cur_cam is None:
            return []
        dist = ctx.pair_distances.get((prev_cam_id, read.camera_id))
        if dist is None:
            dist = haversine_m(prev_cam.lat, prev_cam.lng, cur_cam.lat, cur_cam.lng)
        elapsed = (read.ts - prev_ts).total_seconds()
        if elapsed <= 0:
            return []
        speed_kmh = (dist / elapsed) * 3.6
        threshold = ctx.settings.max_urban_speed_kmh * 1.5
        if speed_kmh <= threshold:
            return []
        return [
            Alert(
                type=AlertType.cloned_plate,
                severity=AlertSeverity.critical,
                plate_norm=read.plate_norm,
                camera_ids=[prev_cam_id, read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "prev_camera": prev_cam_id,
                    "prev_ts": prev_ts.isoformat(),
                    "distance_m": dist,
                    "elapsed_s": elapsed,
                    "required_speed_kmh": speed_kmh,
                    "threshold_kmh": threshold,
                },
                ts=read.ts,
            )
        ]


class ConvoyRule(Rule):
    name = "convoy"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        peers = await ctx.convoy_peers(read.plate_norm, read.camera_id, read.ts)
        if len(peers) < 2:
            return []
        return [
            Alert(
                type=AlertType.convoy,
                severity=AlertSeverity.medium,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "peer_plates": sorted(peers),
                    "common_cameras_min": CONVOY_MIN_COMMON_CAMERAS,
                },
                ts=read.ts,
            )
        ]


class LoiteringRule(Rule):
    name = "loitering"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        zone_id = await ctx.sensitive_zone_id(read.camera_id)
        if zone_id is None:
            return []
        since = read.ts - timedelta(minutes=LOITERING_WINDOW_MIN)
        count = await ctx.plate_sightings_in_zone(read.plate_norm, zone_id, since, read.ts)
        if count < LOITERING_MIN_SIGHTINGS:
            return []
        return [
            Alert(
                type=AlertType.loitering,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "zone_id": zone_id,
                    "sightings": count,
                    "window_min": LOITERING_WINDOW_MIN,
                },
                ts=read.ts,
            )
        ]


def _parse_active_hours(active_hours: Any) -> dict[str, Any]:
    if not active_hours:
        return {}
    if isinstance(active_hours, str):
        try:
            parsed = json.loads(active_hours)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}
    if isinstance(active_hours, dict):
        return active_hours
    return {}


def _in_active_hours(active_hours: Any, ts: datetime) -> bool:
    hours = _parse_active_hours(active_hours)
    if not hours:
        return True

    if "start" in hours and "end" in hours:
        start_h, start_m = (int(part) for part in str(hours["start"]).split(":"))
        end_h, end_m = (int(part) for part in str(hours["end"]).split(":"))
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        cur_min = ts.hour * 60 + ts.minute
        if start_min <= end_min:
            return start_min <= cur_min < end_min
        return cur_min >= start_min or cur_min < end_min

    dow = ts.strftime("%a").lower()[:3]
    hour = ts.hour
    windows = hours.get(dow) or hours.get("default") or []
    for w in windows:
        start = int(w.get("start", 0))
        end = int(w.get("end", 24))
        if start <= hour < end:
            return True
    return False


class GeofenceRule(Rule):
    name = "geofence"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        zone = await ctx.restricted_zone(read.camera_id)
        if zone is None:
            return []
        zone_id, active_hours = zone
        if _in_active_hours(active_hours, read.ts):
            return []
        return [
            Alert(
                type=AlertType.geofence,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "zone_id": zone_id,
                    "active_hours": active_hours,
                },
                ts=read.ts,
            )
        ]


class WrongWayRule(Rule):
    name = "wrong_way"

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        if read.direction is None:
            return []
        cam = ctx.cameras.get(read.camera_id)
        if cam is None or not cam.allowed_direction:
            return []
        allowed = Direction(cam.allowed_direction)
        if read.direction != opposite_direction(allowed):
            return []
        return [
            Alert(
                type=AlertType.wrong_way,
                severity=AlertSeverity.high,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "allowed_direction": allowed.value,
                    "observed_direction": read.direction.value,
                },
                ts=read.ts,
            )
        ]


class PlateVehicleMismatchRule(Rule):
    """Compares OCR vehicle class against registry (Vahan stub)."""

    name = "plate_vehicle_mismatch"

    def __init__(self, registry: RegistryClient) -> None:
        self.registry = registry

    async def evaluate(self, read: PlateRead, ctx: RuleContext) -> list[Alert]:
        record: RegistryRecord = await self.registry.lookup(read.plate_norm)
        if record.status == "unknown" or record.vehicle_class is None:
            return []
        if record.vehicle_class == read.vehicle_class:
            return []
        return [
            Alert(
                type=AlertType.plate_vehicle_mismatch,
                severity=AlertSeverity.medium,
                plate_norm=read.plate_norm,
                camera_ids=[read.camera_id],
                evidence={
                    "reads": [_read_evidence(read)],
                    "registry": {
                        "vehicle_class": record.vehicle_class.value,
                        "color": record.color,
                        "make": record.make,
                        "status": record.status,
                    },
                    "observed_class": read.vehicle_class.value,
                },
                needs_verification=True,
                ts=read.ts,
            )
        ]


class AlertDeduper:
    """Deduplicate alerts per (type, plate) within 10 minutes."""

    def __init__(self) -> None:
        self._recent: dict[tuple[str, str], datetime] = {}

    def is_duplicate(self, alert: Alert, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        key = (alert.type.value, alert.plate_norm.upper())
        last = self._recent.get(key)
        if last and (now - last) < timedelta(minutes=ALERT_DEDUP_MINUTES):
            return True
        self._recent[key] = now
        cutoff = now - timedelta(minutes=ALERT_DEDUP_MINUTES)
        self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        return False


class DefaultRuleContext:
    def __init__(
        self,
        settings: Any,
        cameras: dict[str, CameraInfo],
        watchlist: set[str],
        pairs: list[CameraPair],
        redis: Any,
        pg_pool: Any,
    ) -> None:
        self.settings = settings
        self.cameras = cameras
        self.watchlist = watchlist
        self.redis = redis
        self.pg_pool = pg_pool
        self.pair_distances: dict[tuple[str, str], float] = {
            (p.camera_a, p.camera_b): p.distance_m for p in pairs
        }
        self._loiter_counts: dict[tuple[str, str], list[datetime]] = defaultdict(list)

    async def last_seen(self, plate_norm: str) -> tuple[str, datetime, float] | None:
        data = await self.redis.hgetall(f"lastseen:{plate_norm.upper()}")
        if not data or "camera_id" not in data or "ts" not in data:
            return None
        ts = datetime.fromisoformat(data["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        conf = float(data.get("confidence", "0"))
        return data["camera_id"], ts, conf

    async def convoy_peers(
        self, plate_norm: str, camera_id: str, ts: datetime
    ) -> set[str]:
        zkey = f"convoy:cam:{camera_id}"
        min_score = ts.timestamp() - CONVOY_TIME_WINDOW_SEC
        max_score = ts.timestamp() + CONVOY_TIME_WINDOW_SEC
        members = await self.redis.zrangebyscore(zkey, min_score, max_score)
        plate = plate_norm.upper()
        peers = {m for m in members if m != plate}
        if len(peers) < 2:
            return set()
        lookback = ts.timestamp() - CONVOY_LOOKBACK_MIN * 60
        common: set[str] = set()
        for peer in peers:
            shared = 0
            for cam_id in self.cameras:
                z = f"convoy:cam:{cam_id}"
                a = await self.redis.zscore(z, plate)
                b = await self.redis.zscore(z, peer)
                if (
                    a
                    and b
                    and a >= lookback
                    and b >= lookback
                    and abs(a - b) <= CONVOY_TIME_WINDOW_SEC
                ):
                    shared += 1
            if shared >= CONVOY_MIN_COMMON_CAMERAS:
                common.add(peer)
        return common

    async def sensitive_zone_id(self, camera_id: str) -> str | None:
        from workers.db import camera_in_zone

        return await camera_in_zone(self.pg_pool, camera_id, "sensitive")

    async def restricted_zone(self, camera_id: str) -> tuple[str, dict] | None:
        from workers.db import camera_in_restricted_zone

        return await camera_in_restricted_zone(self.pg_pool, camera_id)

    async def plate_sightings_in_zone(
        self, plate_norm: str, zone_id: str, since: datetime, at: datetime | None = None
    ) -> int:
        key = (plate_norm.upper(), zone_id)
        times = self._loiter_counts[key]
        times.append(at or datetime.now(UTC))
        self._loiter_counts[key] = [t for t in times if t >= since]
        return len(self._loiter_counts[key])
