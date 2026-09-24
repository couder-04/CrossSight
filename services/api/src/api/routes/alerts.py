"""Alert management routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from anpr_common.config import Settings
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from api.db import AlertRow
from api.deps import MinioDep, SessionDep, SettingsDep, UserDep
from api.schemas import AlertActionBody, AlertOut, DispatchBody

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _alert_out(row: AlertRow, crop_url: str | None = None) -> AlertOut:
    return AlertOut(
        id=row.id,
        type=row.type,
        severity=row.severity,
        plate_norm=row.plate_norm,
        camera_ids=list(row.camera_ids or []),
        evidence=row.evidence or {},
        status=row.status,
        needs_verification=row.needs_verification,
        ack_by=row.ack_by,
        dispatched_to=row.dispatched_to,
        closed_note=row.closed_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
        crop_url=crop_url,
    )


def _crop_url(
    minio,
    settings: Settings,
    evidence: dict[str, Any],
) -> str | None:
    crop_key = evidence.get("crop_key")
    if not crop_key:
        reads = evidence.get("reads") or []
        for r in reads:
            if isinstance(r, dict) and r.get("crop_key"):
                crop_key = r["crop_key"]
                break
    if not crop_key:
        return None
    try:
        return minio.presigned_get_object(
            settings.minio_bucket,
            crop_key,
            expires=timedelta(hours=1),
        )
    except Exception:
        return None


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    session: SessionDep,
    _user: UserDep,
    status_filter: str | None = Query(None, alias="status"),
    type_filter: str | None = Query(None, alias="type"),
    severity: str | None = Query(None),
) -> list[AlertOut]:
    stmt = select(AlertRow).order_by(AlertRow.created_at.desc())
    if status_filter:
        stmt = stmt.where(AlertRow.status == status_filter)
    if type_filter:
        stmt = stmt.where(AlertRow.type == type_filter)
    if severity:
        stmt = stmt.where(AlertRow.severity == severity)
    result = await session.execute(stmt)
    return [_alert_out(r) for r in result.scalars()]


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(
    alert_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    _user: UserDep,
) -> AlertOut:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    url = _crop_url(minio, settings, row.evidence or {})
    return _alert_out(row, crop_url=url)


@router.post("/{alert_id}/ack", response_model=AlertOut)
async def acknowledge_alert(
    alert_id: UUID,
    session: SessionDep,
    user: UserDep,
) -> AlertOut:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.status = "acknowledged"
    row.ack_by = user.username
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return _alert_out(row)


@router.post("/{alert_id}/dispatch", response_model=AlertOut)
async def dispatch_alert(
    alert_id: UUID,
    body: DispatchBody,
    session: SessionDep,
    user: UserDep,
) -> AlertOut:
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.status = "dispatched"
    row.dispatched_to = body.dispatched_to
    row.ack_by = row.ack_by or user.username
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return _alert_out(row)


@router.post("/{alert_id}/close", response_model=AlertOut)
async def close_alert(
    alert_id: UUID,
    body: AlertActionBody,
    session: SessionDep,
    user: UserDep,
) -> AlertOut:
    if not body.note or not body.note.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Close requires a note")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    row.status = "closed"
    row.closed_note = body.note.strip()
    row.updated_at = datetime.now(UTC)
    await session.commit()
    return _alert_out(row)
