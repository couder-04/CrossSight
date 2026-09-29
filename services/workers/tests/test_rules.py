"""Unit tests for alert rules using synthetic PlateReads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from anpr_common.schemas import AlertType, Direction, PlateFormat, PlateRead, VehicleClass
from workers.alerts.registry import FileRegistryClient, RegistryRecord
from workers.alerts.rules import (
    AlertDeduper,
    ClonedPlateRule,
    GeofenceRule,
    PlateVehicleMismatchRule,
    RouteAnomalyRule,
    WatchlistRule,
    WrongWayRule,
)
from workers.db import CameraInfo


def _read(
    plate: str = "BR01AB1234",
    camera: str = "cam-1",
    ts: datetime | None = None,
    confidence: float = 0.95,
    direction: Direction | None = Direction.N,
    vehicle_class: VehicleClass = VehicleClass.car,
) -> PlateRead:
    return PlateRead(
        camera_id=camera,
        ts=ts or datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        plate_raw=plate,
        plate_norm=plate,
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=confidence,
        direction=direction,
        vehicle_class=vehicle_class,
        source="simulator",
    )


def _ctx(**overrides):
    base = {
        "settings": SimpleNamespace(max_urban_speed_kmh=120.0),
        "cameras": {
            "cam-1": CameraInfo("cam-1", 18.5, 73.8, 0.0, "N"),
            "cam-2": CameraInfo("cam-2", 18.6, 73.9, 90.0, "E"),
        },
        "watchlist": {"WL01AB9999"},
        "pair_distances": {("cam-1", "cam-2"): 5000.0},
        "last_seen": AsyncMock(return_value=None),
        "convoy_peers": AsyncMock(return_value=set()),
        "sensitive_zone_id": AsyncMock(return_value=None),
        "restricted_zone": AsyncMock(return_value=None),
        "plate_sightings_in_zone": AsyncMock(return_value=0),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_watchlist_exact_match():
    rule = WatchlistRule()
    alerts = await rule.evaluate(_read(plate="WL01AB9999"), _ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.watchlist
    assert alerts[0].severity.value == "high"
    assert alerts[0].needs_verification is False


@pytest.mark.asyncio
async def test_watchlist_fuzzy_match():
    rule = WatchlistRule()
    alerts = await rule.evaluate(_read(plate="WL01AB9998"), _ctx())
    assert len(alerts) == 1
    assert alerts[0].severity.value == "medium"
    assert alerts[0].needs_verification is True


@pytest.mark.asyncio
async def test_cloned_plate_impossible_speed():
    prev_ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    ctx = _ctx(
        last_seen=AsyncMock(return_value=("cam-1", prev_ts, 0.9)),
    )
    rule = ClonedPlateRule()
    read = _read(camera="cam-2", ts=prev_ts + timedelta(seconds=30), confidence=0.9)
    alerts = await rule.evaluate(read, ctx)
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.cloned_plate


@pytest.mark.asyncio
async def test_cloned_plate_rejects_low_confidence():
    prev_ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    ctx = _ctx(last_seen=AsyncMock(return_value=("cam-1", prev_ts, 0.5)))
    rule = ClonedPlateRule()
    read = _read(camera="cam-2", ts=prev_ts + timedelta(seconds=10), confidence=0.9)
    alerts = await rule.evaluate(read, ctx)
    assert alerts == []


@pytest.mark.asyncio
async def test_wrong_way_opposite_direction():
    rule = WrongWayRule()
    read = _read(direction=Direction.S)
    alerts = await rule.evaluate(read, _ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.wrong_way


@pytest.mark.asyncio
async def test_geofence_outside_active_hours():
    rule = GeofenceRule()
    ctx = _ctx(
        restricted_zone=AsyncMock(
            return_value=("zone-r1", {"mon": [{"start": 8, "end": 18}]})
        )
    )
    read = _read(ts=datetime(2025, 6, 2, 22, 0, tzinfo=UTC))
    alerts = await rule.evaluate(read, ctx)
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.geofence


@pytest.mark.asyncio
async def test_plate_vehicle_mismatch_with_registry():
    class MockRegistry:
        async def lookup(self, plate_norm: str) -> RegistryRecord:
            return RegistryRecord(
                plate_norm=plate_norm,
                vehicle_class=VehicleClass.truck,
                status="found",
            )

    rule = PlateVehicleMismatchRule(MockRegistry())
    alerts = await rule.evaluate(_read(vehicle_class=VehicleClass.car), _ctx())
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.plate_vehicle_mismatch


@pytest.mark.asyncio
async def test_route_anomaly_far_from_corridor(tmp_path):
    # Build redis-like mock with zset behaviour.
    store: dict[str, dict[str, float]] = {}

    class FakeRedis:
        async def zadd(self, key, mapping):
            store.setdefault(key, {}).update(mapping)

        async def expire(self, key, _ttl):
            return True

        async def zrange(self, key, _start, _end):
            return list(store.get(key, {}).keys())

    cameras = {
        "cam-1": CameraInfo("cam-1", 18.50, 73.80, 0.0, "N"),
        "cam-2": CameraInfo("cam-2", 18.51, 73.81, 90.0, "E"),
        "cam-3": CameraInfo("cam-3", 18.52, 73.82, 180.0, "S"),
        "cam-far": CameraInfo("cam-far", 18.70, 74.10, 0.0, "N"),  # ~30km away
    }
    # Seed prior cameras in redis.
    key = "plate:cams:BR01AB1234"
    store[key] = {"cam-1": 1.0, "cam-2": 2.0, "cam-3": 3.0}
    ctx = _ctx(
        cameras=cameras,
        redis=FakeRedis(),
        settings=SimpleNamespace(
            max_urban_speed_kmh=120.0,
            route_anomaly_min_cameras=3,
            route_anomaly_distance_m=8000.0,
        ),
        last_seen=AsyncMock(return_value=("cam-3", datetime(2025, 6, 1, 12, 0, tzinfo=UTC), 0.9)),
        pair_distances={},
    )
    rule = RouteAnomalyRule()
    alerts = await rule.evaluate(_read(camera="cam-far"), ctx)
    assert len(alerts) == 1
    assert alerts[0].type == AlertType.route_anomaly


@pytest.mark.asyncio
async def test_file_registry_client(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text('{"MH12XY9999": {"vehicle_class": "truck", "color": "blue"}}')
    client = FileRegistryClient(path)
    found = await client.lookup("MH12XY9999")
    missing = await client.lookup("UNKNOWN1")
    assert found.status == "found"
    assert found.vehicle_class == VehicleClass.truck
    assert missing.status == "unknown"


@pytest.mark.asyncio
async def test_watchlist_exact_includes_crop_key():
    read = PlateRead(
        camera_id="cam-1",
        ts=datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        plate_raw="WL01AB9999",
        plate_norm="WL01AB9999",
        plate_valid=True,
        plate_format=PlateFormat.standard,
        confidence=0.95,
        crop_key="cam-1/abc.jpg",
        source="simulator",
    )
    alerts = await WatchlistRule().evaluate(read, _ctx())
    assert alerts[0].evidence["reads"][0]["crop_key"] == "cam-1/abc.jpg"


def test_alert_deduper_within_10_minutes():
    deduper = AlertDeduper()
    from anpr_common.schemas import Alert, AlertSeverity

    a1 = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="BR01AB1234",
        ts=datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
    )
    a2 = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="BR01AB1234",
        ts=datetime(2025, 1, 1, 10, 5, tzinfo=UTC),
    )
    assert deduper.is_duplicate(a1, datetime(2025, 1, 1, 10, 0, tzinfo=UTC)) is False
    assert deduper.is_duplicate(a2, datetime(2025, 1, 1, 10, 5, tzinfo=UTC)) is True
    a3 = Alert(
        type=AlertType.watchlist,
        severity=AlertSeverity.high,
        plate_norm="BR01AB1234",
        ts=datetime(2025, 1, 1, 10, 11, tzinfo=UTC),
    )
    assert deduper.is_duplicate(a3, datetime(2025, 1, 1, 10, 11, tzinfo=UTC)) is False
