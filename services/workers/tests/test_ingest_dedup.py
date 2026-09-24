"""Unit tests for ingest deduplication logic."""

import time

import pytest
from workers.dedup import DEDUP_WINDOW_SEC, HybridDedup, InMemoryDedup


class FakeRedisDedup:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def is_duplicate(self, plate_norm: str, camera_id: str) -> bool:
        key = f"{plate_norm}:{camera_id}"
        if key in self.keys:
            return True
        self.keys.add(key)
        return False


@pytest.mark.asyncio
async def test_in_memory_dedup_within_window():
    dedup = InMemoryDedup()
    t0 = 1_000_000.0
    assert dedup.is_duplicate("BR01AB1234", "cam-1", t0) is False
    assert dedup.is_duplicate("BR01AB1234", "cam-1", t0 + 5) is True
    assert dedup.is_duplicate("BR01AB1234", "cam-2", t0 + 5) is False


@pytest.mark.asyncio
async def test_in_memory_dedup_expires_after_window():
    dedup = InMemoryDedup()
    t0 = time.time()
    assert dedup.is_duplicate("DL3CAB1234", "cam-1", t0) is False
    assert dedup.is_duplicate("DL3CAB1234", "cam-1", t0 + DEDUP_WINDOW_SEC + 1) is False


@pytest.mark.asyncio
async def test_hybrid_dedup_uses_memory_first():
    memory = InMemoryDedup()
    redis = FakeRedisDedup()
    hybrid = HybridDedup(memory, redis)  # type: ignore[arg-type]
    t0 = 1_000_000.0
    assert await hybrid.is_duplicate("MH12AB1234", "cam-3", t0) is False
    assert await hybrid.is_duplicate("MH12AB1234", "cam-3", t0 + 2) is True


@pytest.mark.asyncio
async def test_hybrid_dedup_without_redis():
    memory = InMemoryDedup()
    hybrid = HybridDedup(memory, None)
    t0 = 1_000_000.0
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0) is False
    assert await hybrid.is_duplicate("KA01AB1234", "cam-9", t0 + 1) is True
