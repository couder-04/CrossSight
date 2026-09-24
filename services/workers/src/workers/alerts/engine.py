"""Alert engine: run rules, dedupe, persist, publish."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Sequence

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from anpr_common.config import Settings, get_settings
from anpr_common.schemas import Alert, PlateRead
from pydantic import ValidationError

from workers.alerts.registry import NoopRegistryClient, RegistryClient
from workers.alerts.rules import (
    AlertDeduper,
    ClonedPlateRule,
    ConvoyRule,
    DefaultRuleContext,
    GeofenceRule,
    LoiteringRule,
    PlateVehicleMismatchRule,
    Rule,
    WatchlistRule,
    WrongWayRule,
)
from workers.db import (
    create_pg_pool,
    create_redis,
    load_camera_pairs,
    load_cameras,
    load_watchlist,
    persist_alert,
)
from workers.health import HealthServer

logger = logging.getLogger(__name__)


class AlertEngine:
    def __init__(
        self,
        rules: Sequence[Rule],
        ctx: DefaultRuleContext,
        deduper: AlertDeduper | None = None,
    ) -> None:
        self.rules = list(rules)
        self.ctx = ctx
        self.deduper = deduper or AlertDeduper()

    async def process(self, read: PlateRead) -> list[Alert]:
        alerts: list[Alert] = []
        for rule in self.rules:
            try:
                hits = await rule.evaluate(read, self.ctx)
            except Exception:
                logger.exception("Rule %s failed", rule.name)
                continue
            for alert in hits:
                if not self.deduper.is_duplicate(alert, read.ts.replace(tzinfo=None)):
                    alerts.append(alert)
        return alerts


def build_rules(registry: RegistryClient | None = None) -> list[Rule]:
    reg = registry or NoopRegistryClient()
    return [
        WatchlistRule(),
        ClonedPlateRule(),
        ConvoyRule(),
        LoiteringRule(),
        GeofenceRule(),
        WrongWayRule(),
        PlateVehicleMismatchRule(reg),
    ]


class AlertsWorker:
    def __init__(
        self,
        settings: Settings | None = None,
        health_port: int = 8083,
        registry: RegistryClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.health_port = health_port
        self.registry = registry or NoopRegistryClient()
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._health = HealthServer("alerts", health_port)
        self._stop: asyncio.Event | None = None
        self._engine: AlertEngine | None = None
        self._redis = None
        self._pg_pool = None

    async def _init(self) -> None:
        self._pg_pool = await create_pg_pool(self.settings)
        cameras = await load_cameras(self._pg_pool)
        pairs = await load_camera_pairs(self._pg_pool, adjacent_only=False)
        watchlist = await load_watchlist(self._pg_pool)
        self._redis = await create_redis(self.settings)
        ctx = DefaultRuleContext(
            self.settings, cameras, watchlist, pairs, self._redis, self._pg_pool
        )
        self._engine = AlertEngine(build_rules(self.registry), ctx)

    async def _publish_alert(self, alert: Alert) -> None:
        assert self._producer is not None and self._redis is not None and self._pg_pool
        payload = alert.model_dump_json().encode()
        await self._producer.send_and_wait(self.settings.topic_alerts, payload)
        await self._redis.publish("alerts", alert.model_dump_json())
        await persist_alert(
            self._pg_pool,
            {
                "id": str(alert.id),
                "type": alert.type.value,
                "severity": alert.severity.value,
                "plate_norm": alert.plate_norm,
                "camera_ids": alert.camera_ids,
                "evidence": alert.evidence,
                "status": alert.status.value,
                "needs_verification": alert.needs_verification,
                "ts": alert.ts,
            },
        )

    async def _prime_convoy_state(self, read: PlateRead) -> None:
        assert self._redis is not None
        plate = read.plate_norm.upper()
        ts_epoch = read.ts.timestamp()
        zkey = f"convoy:cam:{read.camera_id}"
        await self._redis.zadd(zkey, {plate: ts_epoch})
        await self._redis.expire(zkey, 1800)

    async def _record_last_seen(self, read: PlateRead) -> None:
        assert self._redis is not None
        from datetime import UTC

        plate = read.plate_norm.upper()
        await self._redis.hset(
            f"lastseen:{plate}",
            mapping={
                "camera_id": read.camera_id,
                "ts": read.ts.replace(tzinfo=UTC).isoformat(),
                "confidence": str(read.confidence),
            },
        )

    async def run(self) -> None:
        logging.basicConfig(level=logging.INFO)
        self._stop = asyncio.Event()
        await self._health.start()
        await self._init()

        self._consumer = AIOKafkaConsumer(
            self.settings.topic_reads,
            bootstrap_servers=self.settings.kafka_bootstrap,
            group_id="anpr-alerts",
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.settings.kafka_bootstrap,
        )
        await self._consumer.start()
        await self._producer.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass

        assert self._engine is not None
        try:
            async for msg in self._consumer:
                if self._stop.is_set():
                    break
                try:
                    read = PlateRead.model_validate(orjson.loads(msg.value))
                except (ValidationError, orjson.JSONDecodeError):
                    continue
                await self._prime_convoy_state(read)
                for alert in await self._engine.process(read):
                    await self._publish_alert(alert)
                await self._record_last_seen(read)
        finally:
            if self._consumer:
                await self._consumer.stop()
            if self._producer:
                await self._producer.stop()
            if self._pg_pool:
                await self._pg_pool.close()
            if self._redis:
                await self._redis.aclose()
            await self._health.stop()
