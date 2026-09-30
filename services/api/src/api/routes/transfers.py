"""Uploads, imports, exports, and evidence downloads."""

from __future__ import annotations

import io
import json
import logging
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from anpr_common.config import get_settings
from anpr_common.intelligence.access import export_action, role_can
from anpr_common.intelligence.exporters import export_filename, overlay_plan, rows_to_csv, rows_to_json, rows_to_pdf
from anpr_common.intelligence.filesafety import object_key, validate_upload
from anpr_common.intelligence.importers import (
    preview_calibration,
    preview_cameras,
    preview_registry,
    preview_watchlist,
    preview_zones,
)
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from geoalchemy2 import WKTElement
from shapely.geometry import shape
from sqlalchemy import select

from api.auditutil import audit_row
from api.jobs import FAILED, requeue
from api.db import (
    AlertRow,
    Camera,
    CameraCalibrationRow,
    ExportRow,
    UploadRow,
    VehicleRegistryRow,
    WatchlistEntry,
    Zone,
    get_session_factory,
)
from api.deps import (
    MinioDep,
    SessionDep,
    SettingsDep,
    UserDep,
    get_clickhouse,
    get_minio,
)
from api.media import decode_image, reads_from_frames, recognize_bgr, sample_video

logger = logging.getLogger(__name__)
router = APIRouter(tags=["transfers"])

_IMPORT_ACTION = {
    "watchlist": "import_watchlist",
    "registry": "import_registry",
    "cameras": "import_cameras",
    "calibration": "import_calibration",
    "zones": "import_zones",
}
_MEDIA_ACTION = {"video": "upload_video", "image": "upload_image"}
_EXPORT_KINDS = {
    "traffic",
    "od",
    "travel",
    "dwell",
    "vehicles",
    "incidents",
    "camera_health",
    "enforcement",
    "sightings",
    "investigation",
    "watchlist",
    "registry",
    "flow",
}


def _forbid(role: str, action: str) -> None:
    if not role_can(role, action):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is too large")
    return data


def _store(minio, settings, key: str, data: bytes, content_type: str) -> None:
    if not minio.bucket_exists(settings.minio_bucket):
        minio.make_bucket(settings.minio_bucket)
    minio.put_object(settings.minio_bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)


def _upload_out(row: UploadRow) -> dict[str, Any]:
    preview = row.preview or {}
    return {
        "id": str(row.id),
        "kind": row.kind,
        "filename": row.filename,
        "status": row.status,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "size_bytes": row.size_bytes,
        "camera_id": row.camera_id,
        "error": row.error,
        "valid_count": preview.get("valid_count"),
        "invalid_count": preview.get("invalid_count"),
        "duplicate_count": preview.get("duplicate_count"),
        "result": row.result or {},
    }


@router.post("/imports/preview")
async def preview_import(
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    action = _IMPORT_ACTION.get(kind)
    if action is None:
        raise HTTPException(status_code=400, detail="Unknown import kind")
    _forbid(user.role.value, action)
    data = await _read_limited(file, settings.max_upload_table_bytes)
    file_kind = "geojson" if kind == "zones" else "json" if kind == "calibration" else "csv"
    if kind == "cameras" and (file.filename or "").lower().endswith(".json"):
        file_kind = "json"
    try:
        meta = validate_upload(
            filename=file.filename or "upload.csv",
            data=data,
            kind=file_kind if kind != "cameras" else ("json" if file_kind == "json" else "csv"),
            max_bytes=settings.max_upload_table_bytes,
            claimed_type=file.content_type,
        )
        text = data.decode("utf-8-sig")
        preview = await _build_preview(session, kind, text, file_kind == "json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = UploadRow(
        kind=kind,
        filename=meta["filename"],
        mime=meta["mime"],
        size_bytes=meta["size"],
        status="preview",
        uploaded_by=user.username,
        preview=preview,
    )
    session.add(row)
    await session.flush()
    key = object_key("imports", str(row.id), meta["filename"])
    _store(minio, settings, key, data, meta["mime"])
    row.object_key = key
    session.add(audit_row(user, "import_preview", params={"id": str(row.id), "kind": kind, **_counts(preview)}))
    await session.commit()
    await session.refresh(row)
    return _upload_out(row) | {"preview": preview}


async def _build_preview(session, kind: str, text: str, as_json: bool) -> dict:
    if kind == "watchlist":
        existing = {row.plate_norm for row in (await session.execute(select(WatchlistEntry))).scalars()}
        return preview_watchlist(text, existing)
    if kind == "registry":
        existing = {row.plate_norm for row in (await session.execute(select(VehicleRegistryRow))).scalars()}
        return preview_registry(text, existing)
    if kind == "cameras":
        existing = {row[0] for row in (await session.execute(select(Camera.id))).all()}
        return preview_cameras(text, fmt="json" if as_json else "csv", existing=existing)
    if kind == "calibration":
        return preview_calibration(text)
    return preview_zones(text)


def _counts(preview: dict) -> dict[str, int]:
    return {
        "valid": int(preview.get("valid_count") or 0),
        "invalid": int(preview.get("invalid_count") or 0),
        "duplicates": int(preview.get("duplicate_count") or 0),
    }


@router.post("/imports/{upload_id}/confirm")
async def confirm_import(
    upload_id: UUID,
    session: SessionDep,
    user: UserDep,
    acknowledge_invalid: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    row = await session.get(UploadRow, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    _forbid(user.role.value, _IMPORT_ACTION.get(row.kind, ""))
    if row.status != "preview":
        raise HTTPException(status_code=409, detail="Import is not waiting for confirmation")
    preview = row.preview or {}
    if preview.get("invalid_count") and not acknowledge_invalid:
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid rows must be acknowledged", "invalid": preview.get("invalid")},
        )
    applied = await _apply_preview(session, row.kind, preview, user.username, overwrite)
    row.status = "completed"
    row.result = {"applied": applied, "acknowledged_invalid": acknowledge_invalid, "overwrite": overwrite}
    row.updated_at = datetime.now(UTC)
    session.add(audit_row(user, "import_confirm", params={"id": str(row.id), "kind": row.kind, "applied": applied}))
    await session.commit()
    return _upload_out(row)


async def _apply_preview(session, kind: str, preview: dict, username: str, overwrite: bool) -> int:
    applied = 0
    if kind == "watchlist":
        for item in preview.get("valid") or []:
            if await session.get(WatchlistEntry, item["plate_norm"]) is not None:
                continue
            session.add(
                WatchlistEntry(
                    plate_norm=item["plate_norm"],
                    reason=item["reason"],
                    severity=item["severity"],
                    added_by=username,
                    expires_at=_dt(item.get("valid_until")),
                    notes=item.get("notes"),
                    priority=item.get("severity"),
                )
            )
            applied += 1
    elif kind == "registry":
        for item in preview.get("valid") or []:
            current = await session.get(VehicleRegistryRow, item["plate_norm"])
            payload = dict(item)
            payload["updated_by"] = username
            payload["valid_from"] = _dt(item.get("valid_from"))
            payload["valid_until"] = _dt(item.get("valid_until"))
            if current is None:
                session.add(VehicleRegistryRow(**payload))
            else:
                for key, value in payload.items():
                    setattr(current, key, value)
            applied += 1
    elif kind == "cameras":
        records = list(preview.get("valid") or [])
        if overwrite:
            records.extend(item for item in preview.get("duplicates") or [] if item.get("exists"))
        for item in records:
            await _upsert_camera(session, item)
            applied += 1
    elif kind == "calibration":
        for item in preview.get("valid") or []:
            current = await session.get(CameraCalibrationRow, item["camera_id"])
            if current is None:
                session.add(
                    CameraCalibrationRow(
                        camera_id=item["camera_id"],
                        homography=item.get("homography"),
                        coordinate_reference=item.get("coordinate_reference"),
                        lanes=item.get("lanes"),
                        speed_calibration=item.get("speed_calibration"),
                        updated_by=username,
                    )
                )
            else:
                current.homography = item.get("homography")
                current.coordinate_reference = item.get("coordinate_reference")
                current.lanes = item.get("lanes")
                current.speed_calibration = item.get("speed_calibration")
                current.updated_by = username
            applied += 1
    elif kind == "zones":
        for item in preview.get("valid") or []:
            geom = WKTElement(shape(item["geojson"]).wkt, srid=4326)
            current = await session.get(Zone, item["id"])
            if current is None:
                session.add(
                    Zone(
                        id=item["id"],
                        name=item["name"],
                        kind=item["kind"],
                        geom=geom,
                        active_hours=item.get("active_hours"),
                    )
                )
            else:
                current.name = item["name"]
                current.kind = item["kind"]
                current.geom = geom
                current.active_hours = item.get("active_hours")
            applied += 1
    return applied


async def _upsert_camera(session, item: dict) -> None:
    current = await session.get(Camera, item["camera_id"])
    geom = WKTElement(f"POINT({item['longitude']} {item['latitude']})", srid=4326)
    if current is None:
        session.add(
            Camera(
                id=item["camera_id"],
                name=item["name"],
                geom=geom,
                allowed_direction=item.get("direction"),
                status=item.get("status") or "active",
                ops_config={"stream_ref": item.get("stream_ref"), "zone": item.get("zone")},
            )
        )
        return
    current.name = item["name"]
    current.geom = geom
    current.allowed_direction = item.get("direction")
    current.status = item.get("status") or current.status
    current.ops_config = {**(current.ops_config or {}), "stream_ref": item.get("stream_ref"), "zone": item.get("zone")}


def _dt(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


@router.get("/imports")
async def list_imports(session: SessionDep, _user: UserDep, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    result = await session.execute(select(UploadRow).order_by(UploadRow.created_at.desc()).limit(limit))
    return [_upload_out(row) for row in result.scalars()]


@router.get("/imports/{upload_id}")
async def get_import(upload_id: UUID, session: SessionDep, _user: UserDep) -> dict:
    row = await session.get(UploadRow, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return _upload_out(row) | {"preview": row.preview or {}}


@router.post("/uploads/media")
async def upload_media(
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
    kind: str = Form(...),
    camera_id: str = Form(...),
    file: UploadFile = File(...),
    captured_at: str | None = Form(None),
    zone_id: str | None = Form(None),
) -> dict[str, Any]:
    action = _MEDIA_ACTION.get(kind)
    if action is None:
        raise HTTPException(status_code=400, detail="kind must be video or image")
    _forbid(user.role.value, action)
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=400, detail="Unknown camera")
    limit = settings.max_upload_video_bytes if kind == "video" else settings.max_upload_image_bytes
    data = await _read_limited(file, limit)
    try:
        meta = validate_upload(
            filename=file.filename or f"upload.{ 'mp4' if kind == 'video' else 'jpg'}",
            data=data,
            kind=kind,
            max_bytes=limit,
            claimed_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = UploadRow(
        kind=kind,
        filename=meta["filename"],
        mime=meta["mime"],
        size_bytes=meta["size"],
        status="queued",
        uploaded_by=user.username,
        camera_id=camera_id,
        zone_id=zone_id,
        captured_at=_dt(captured_at) if captured_at else datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    key = object_key(f"uploads/{kind}", str(row.id), meta["filename"])
    _store(minio, settings, key, data, meta["mime"])
    row.object_key = key
    session.add(audit_row(user, "media_upload", params={"id": str(row.id), "kind": kind, "camera_id": camera_id}))
    await session.commit()
    background.add_task(_process_media, row.id)
    await session.refresh(row)
    return _upload_out(row)


async def _process_media(upload_id: UUID) -> None:
    settings = get_settings()
    factory = get_session_factory()
    started = datetime.now(UTC)
    async with factory() as session:
        row = await session.get(UploadRow, upload_id)
        if row is None or not row.object_key:
            return
        row.status = "processing"
        row.updated_at = started
        await session.commit()
        try:
            client = get_minio(settings)
            obj = client.get_object(settings.minio_bucket, row.object_key)
            data = obj.read()
            obj.close()
            obj.release_conn()
            if row.kind == "image":
                found = recognize_bgr(decode_image(data))
                result = {
                    "reads": [
                        {
                            "plate": found["plate"],
                            "confidence": found["confidence"],
                            "ts": (row.captured_at or started).isoformat(),
                            "camera_id": row.camera_id,
                        }
                    ],
                    "source_key": row.object_key,
                }
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "clip"
                    path.write_bytes(data)
                    frames, fps = sample_video(str(path))
                    result = {
                        "reads": reads_from_frames(
                            frames,
                            camera_id=row.camera_id or "",
                            started_at=row.captured_at or started,
                            source_key=row.object_key,
                            fps=fps,
                            every_n=15,
                        ),
                        "frames": len(frames),
                        "source_key": row.object_key,
                    }
            try:
                result["published"] = await _publish_reads(settings, result["reads"], row)
                row.status = "completed"
                row.error = None
            except Exception as exc:
                result["publish_error"] = str(exc)[:500]
                row.status = "failed"
                row.error = f"OCR finished but Kafka publish failed: {exc}"[:500]
            row.result = result
        except Exception as exc:
            logger.exception("media processing failed")
            row.status = "failed"
            row.error = str(exc)[:500]
        row.updated_at = datetime.now(UTC)
        row.result = {**(row.result or {}), "duration_s": (row.updated_at - started).total_seconds()}
        await session.commit()


async def _publish_reads(settings, reads: list[dict], row: UploadRow) -> int:
    publishable = [item for item in reads if item.get("plate") and item.get("plate") != "UNKNOWN" and item.get("ts")]
    if not publishable:
        return 0
    from aiokafka import AIOKafkaProducer
    from anpr_common.grammar import normalize_plate
    from anpr_common.schemas import PlateRead

    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
    await producer.start()
    sent = 0
    try:
        for item in publishable:
            grammar = normalize_plate(str(item.get("plate") or ""))
            if not grammar.norm:
                continue
            confidence = float(item.get("confidence") or 0)
            event = PlateRead(
                camera_id=row.camera_id or item.get("camera_id") or "cam-upload",
                ts=datetime.fromisoformat(item["ts"]),
                plate_raw=grammar.norm,
                plate_norm=grammar.norm,
                plate_valid=grammar.valid,
                plate_format=grammar.format,  # type: ignore[arg-type]
                confidence=min(1.0, max(0.0, confidence)),
                source="ocr",
                source_video_key=row.object_key,
            )
            await producer.send_and_wait(settings.topic_reads, event.model_dump_json().encode())
            sent += 1
    finally:
        await producer.stop()
    return sent


@router.post("/exports")
async def create_export(
    background: BackgroundTasks,
    session: SessionDep,
    user: UserDep,
    kind: str = Form(...),
    fmt: str = Form(...),
    filters: str = Form("{}"),
) -> dict[str, Any]:
    if kind not in _EXPORT_KINDS:
        raise HTTPException(status_code=400, detail="Unknown export kind")
    if fmt not in {"csv", "pdf", "json"}:
        raise HTTPException(status_code=400, detail="format must be csv, pdf, or json")
    _forbid(user.role.value, export_action(kind))
    try:
        parsed = json.loads(filters)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="filters must be JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")
    now = datetime.now(UTC)
    row = ExportRow(
        kind=kind,
        format=fmt,
        status="queued",
        requested_by=user.username,
        filters=parsed,
        expires_at=now + timedelta(days=7),
    )
    session.add(row)
    await session.flush()
    row.filename = export_filename(kind, fmt, now, str(row.id))
    session.add(audit_row(user, "export_create", params={"id": str(row.id), "kind": kind, "format": fmt, "filters": parsed}))
    await session.commit()
    background.add_task(_run_export, row.id, user.role.value)
    await session.refresh(row)
    return _export_out(row)


async def _run_export(export_id: UUID, role: str) -> None:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(ExportRow, export_id)
        if row is None:
            return
        row.status = "processing"
        await session.commit()
        try:
            headers, records, sections = await _collect(session, get_clickhouse(settings), settings, row.kind, row.filters, role)
            if row.format == "csv":
                payload = rows_to_csv(headers, records)
                content_type = "text/csv; charset=utf-8"
            elif row.format == "json":
                payload = rows_to_json({"kind": row.kind, "filters": row.filters, "headers": headers, "rows": records})
                content_type = "application/json"
            else:
                payload = rows_to_pdf(
                    row.kind.replace("_", " ").title(),
                    [f"Generated {datetime.now(UTC).isoformat()}", f"Filters {json.dumps(row.filters, default=str)}"],
                    sections,
                )
                content_type = "application/pdf"
            key = object_key("exports", str(row.id), row.filename or "export.bin")
            _store(get_minio(settings), settings, key, payload, content_type)
            row.object_key = key
            row.status = "ready"
            row.error = None
        except Exception as exc:
            logger.exception("export failed")
            row.status = "failed"
            row.error = str(exc)[:500]
        await session.commit()


@router.post("/uploads/{upload_id}/retry")
async def retry_upload(
    upload_id: UUID,
    background: BackgroundTasks,
    session: SessionDep,
    _user: UserDep,
) -> dict[str, Any]:
    row = await session.get(UploadRow, upload_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    if row.status != FAILED:
        raise HTTPException(status_code=409, detail="Only a failed upload can be retried")
    requeue(row)
    await session.commit()
    background.add_task(_process_media, row.id)
    await session.refresh(row)
    return _upload_out(row)


@router.post("/exports/{export_id}/retry")
async def retry_export(
    export_id: UUID,
    background: BackgroundTasks,
    session: SessionDep,
    user: UserDep,
) -> dict[str, Any]:
    row = await session.get(ExportRow, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    if row.status != FAILED:
        raise HTTPException(status_code=409, detail="Only a failed export can be retried")
    _forbid(user.role.value, export_action(row.kind))
    requeue(row)
    await session.commit()
    background.add_task(_run_export, row.id, user.role.value)
    await session.refresh(row)
    return _export_out(row)


async def _collect(session, ch, settings, kind: str, filters: dict, role: str):
    from api.routes.platform import camera_health, camera_od, dwell, travel_times, vehicle_classes

    class _User:
        role = type("R", (), {"value": role})()
        id = ""
        username = "export"

    start = _dt(filters.get("start")) if filters.get("start") else None
    end = _dt(filters.get("end")) if filters.get("end") else None
    if kind in {"od", "flow"}:
        data = await camera_od(ch, session, settings, _User(), start, end)
        headers = ["origin", "destination", "trip_count", "unique_vehicles"]
        records = [[c["origin"], c["destination"], c["trip_count"], c["unique_vehicles"]] for c in data["cells"]]
    elif kind == "travel":
        data = await travel_times(ch, settings, _User(), start, end, filters.get("origin"), filters.get("destination"))
        headers = ["origin", "destination", "count", "avg_s", "median_s", "min_s", "max_s", "p90_s"]
        records = [[r.get(h) for h in headers] for r in data["routes"]]
    elif kind == "dwell":
        data = await dwell(ch, settings, _User(), start, end, filters.get("camera_id"))
        headers = ["plate", "camera_id", "dwell_s", "classification", "entry_ts", "exit_ts"]
        records = [[s.get(h) for h in headers] for s in data["sessions"]]
    elif kind in {"vehicles", "traffic"}:
        data = await vehicle_classes(ch, _User(), start, end, filters.get("camera_id"))
        headers = ["vehicle_class", "count", "share"]
        records = [[k, data["counts"].get(k, 0), data["shares"].get(k, 0)] for k in data["counts"]]
    elif kind == "camera_health":
        data = await camera_health(ch, session, settings, _User(), start, end)
        headers = ["camera_id", "state", "last_read", "age_s", "read_rate_per_min"]
        records = [[c[h] for h in headers] for c in data["cameras"]]
    elif kind in {"incidents", "enforcement"}:
        stmt = select(AlertRow).order_by(AlertRow.created_at.desc()).limit(5000)
        if filters.get("status"):
            stmt = stmt.where(AlertRow.status == filters["status"])
        if filters.get("type"):
            stmt = stmt.where(AlertRow.type == filters["type"])
        rows = list((await session.execute(stmt)).scalars())
        headers = ["id", "type", "status", "severity", "plate_norm", "created_at"]
        records = [[str(r.id), r.type, r.status, r.severity, r.plate_norm, r.created_at.isoformat()] for r in rows]
        data = {"rows": len(records)}
    elif kind == "watchlist":
        rows = list((await session.execute(select(WatchlistEntry))).scalars())
        headers = ["plate_norm", "reason", "severity", "expires_at"]
        records = [[r.plate_norm, r.reason, r.severity, r.expires_at.isoformat() if r.expires_at else ""] for r in rows]
        data = {}
    elif kind == "registry":
        rows = list((await session.execute(select(VehicleRegistryRow))).scalars())
        headers = ["plate_norm", "vehicle_class", "make", "model", "color", "registration_status", "owner_ref"]
        records = [[getattr(r, h) for h in headers] for r in rows]
        data = {}
    else:
        plate = str(filters.get("plate") or "")
        if not plate:
            raise ValueError("investigation export requires a plate filter")
        from api.routes.platform import investigation

        data = await investigation(ch, session, _User(), plate, filters.get("camera_id"), start, end)
        headers = ["camera_id", "ts", "plate_norm", "confidence", "vehicle_class"]
        records = [[s.get(h) for h in headers] for s in data["sightings"]]
    lines = [", ".join(str(cell) for cell in record) for record in records[:40]]
    sections = [(kind, lines or ["No rows for the selected filters"])]
    return headers, records, sections


@router.get("/exports")
async def list_exports(session: SessionDep, _user: UserDep, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    result = await session.execute(select(ExportRow).order_by(ExportRow.created_at.desc()).limit(limit))
    return [_export_out(row) for row in result.scalars()]


@router.get("/exports/{export_id}")
async def get_export(export_id: UUID, session: SessionDep, _user: UserDep) -> dict:
    row = await session.get(ExportRow, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    return _export_out(row)


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
):
    row = await session.get(ExportRow, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Export not found")
    _forbid(user.role.value, export_action(row.kind))
    if row.expires_at and row.expires_at < datetime.now(UTC):
        row.status = "expired"
        await session.commit()
        raise HTTPException(status_code=410, detail="Export expired")
    if row.status != "ready" or not row.object_key:
        raise HTTPException(status_code=409, detail="Export is not ready")
    session.add(audit_row(user, "export_download", params={"id": str(row.id), "kind": row.kind}))
    await session.commit()
    obj = minio.get_object(settings.minio_bucket, row.object_key)
    media = "text/csv" if row.format == "csv" else "application/pdf" if row.format == "pdf" else "application/json"
    return StreamingResponse(
        obj.stream(32 * 1024),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{row.filename}"'},
    )


def _export_out(row: ExportRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "format": row.format,
        "status": row.status,
        "requested_by": row.requested_by,
        "requested_at": row.created_at.isoformat() if row.created_at else None,
        "filters": row.filters or {},
        "filename": row.filename,
        "error": row.error,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


@router.get("/alerts/{alert_id}/evidence")
async def list_evidence(alert_id: UUID, session: SessionDep, user: UserDep) -> dict:
    _forbid(user.role.value, "download_evidence")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    evidence = row.evidence or {}
    items = []
    crop = evidence.get("crop_key")
    reads = evidence.get("reads") or []
    if not crop:
        for read in reads:
            if isinstance(read, dict) and read.get("crop_key"):
                crop = read["crop_key"]
                break
    if crop:
        items.append({"kind": "crop", "available": True})
    source = evidence.get("source_video_key")
    for read in reads:
        if isinstance(read, dict) and read.get("source_video_key"):
            source = read["source_video_key"]
    if source:
        items.append({"kind": "video", "available": True})
    return {"alert_id": str(alert_id), "items": items}


@router.get("/alerts/{alert_id}/evidence/{kind}")
async def download_evidence(
    alert_id: UUID,
    kind: str,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
):
    _forbid(user.role.value, "download_evidence")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    evidence = row.evidence or {}
    key = None
    if kind == "crop":
        key = evidence.get("crop_key")
        for read in evidence.get("reads") or []:
            if not key and isinstance(read, dict):
                key = read.get("crop_key")
        media = "image/jpeg"
        filename = f"crosssight_{alert_id}_crop.jpg"
    elif kind == "video":
        key = evidence.get("source_video_key")
        for read in evidence.get("reads") or []:
            if not key and isinstance(read, dict):
                key = read.get("source_video_key")
        media = "video/mp4"
        filename = f"crosssight_{alert_id}_source.mp4"
    else:
        raise HTTPException(status_code=400, detail="kind must be crop or video")
    if not key:
        raise HTTPException(status_code=404, detail="This incident has no stored evidence of that kind")
    session.add(audit_row(user, "evidence_download", plate_norm=row.plate_norm, params={"alert_id": str(alert_id), "kind": kind}))
    await session.commit()
    obj = minio.get_object(settings.minio_bucket, key)
    return StreamingResponse(obj.stream(32 * 1024), media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/alerts/{alert_id}/evidence/clip")
async def export_clip(
    alert_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    minio: MinioDep,
    user: UserDep,
    before_s: float = 5,
    after_s: float = 5,
) -> dict[str, Any]:
    _forbid(user.role.value, "download_evidence")
    if before_s < 0 or after_s < 0 or before_s + after_s > 120:
        raise HTTPException(status_code=400, detail="Clip window must be between 0 and 120 seconds")
    row = await session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    evidence = row.evidence or {}
    source = evidence.get("source_video_key")
    meta = {"plate": row.plate_norm, "alert": row.type, "bbox": evidence.get("bbox"), "track_id": evidence.get("track_id")}
    for read in evidence.get("reads") or []:
        if isinstance(read, dict):
            source = source or read.get("source_video_key")
            meta["bbox"] = meta["bbox"] or read.get("bbox")
            meta["track_id"] = meta["track_id"] if meta["track_id"] is not None else read.get("track_id")
    plan = overlay_plan(meta)
    if not source:
        raise HTTPException(status_code=409, detail="Source video is not stored for this incident")
    session.add(
        audit_row(
            user,
            "evidence_clip",
            plate_norm=row.plate_norm,
            params={"alert_id": str(alert_id), "before_s": before_s, "after_s": after_s, "overlay": plan},
        )
    )
    await session.commit()
    return {
        "alert_id": str(alert_id),
        "source_available": True,
        "before_s": before_s,
        "after_s": after_s,
        "overlay": plan,
        "download": f"/alerts/{alert_id}/evidence/video",
    }
