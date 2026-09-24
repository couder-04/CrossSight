"""Vehicle registry client interface (Vahan integration stub)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from anpr_common.schemas import VehicleClass


@dataclass(frozen=True)
class RegistryRecord:
    plate_norm: str
    vehicle_class: VehicleClass | None = None
    color: str | None = None
    make: str | None = None
    status: str = "unknown"


class RegistryClient(Protocol):
    """Lookup registered vehicle attributes for a plate.

    A production deployment would call the Vahan / state RTO API here.
    """

    async def lookup(self, plate_norm: str) -> RegistryRecord: ...


class NoopRegistryClient:
    """Default no-op implementation — always returns unknown."""

    async def lookup(self, plate_norm: str) -> RegistryRecord:
        return RegistryRecord(plate_norm=plate_norm.upper(), status="unknown")
