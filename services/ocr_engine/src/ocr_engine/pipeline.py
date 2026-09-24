"""Video OCR pipeline: detect → track → read → fuse → publish."""

from __future__ import annotations

import io
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
from anpr_common.config import Settings
from anpr_common.geo import bearing_to_direction
from anpr_common.grammar import normalize_plate
from anpr_common.schemas import Direction, PlateFormat, PlateRead, VehicleClass

from ocr_engine.detect import (
    PlateDetector,
    VehicleDetector,
    compute_quality,
    is_two_row_plate,
    rectify_plate,
    validate_plate_weights,
)
from ocr_engine.enhance import NoopEnhancer, enhance_plate
from ocr_engine.fusion import FrameRead, fuse_track_reads
from ocr_engine.recognize import MockRecognizer, ParseqRecognizer, Recognizer

logger = logging.getLogger(__name__)

VEHICLE_CLASS_MAP = {
    "car": VehicleClass.car,
    "motorcycle": VehicleClass.motorcycle,
    "bus": VehicleClass.bus,
    "truck": VehicleClass.truck,
}


@dataclass
class TrackState:
    track_id: int
    vehicle_class: str = "car"
    centers: list[tuple[float, float]] = field(default_factory=list)
    frame_reads: list[FrameRead] = field(default_factory=list)
    best_crop: np.ndarray | None = None
    best_quality: float = 0.0
    frames_seen: int = 0


def motion_bearing_deg(centers: list[tuple[float, float]]) -> float | None:
    if len(centers) < 2:
        return None
    x1, y1 = centers[0]
    x2, y2 = centers[-1]
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) < 1e-3 and abs(dy) < 1e-3:
        return None
    # Image coords: x right, y down — convert to compass bearing (0=N).
    angle = math.degrees(math.atan2(dx, -dy))
    return (angle + 360.0) % 360.0


def estimate_direction(
    centers: list[tuple[float, float]], camera_heading_deg: float
) -> Direction | None:
    bearing = motion_bearing_deg(centers)
    if bearing is None:
        return None
    travel = (bearing + camera_heading_deg) % 360.0
    return bearing_to_direction(travel)


class MinioUploader:
    def __init__(self, settings: Settings) -> None:
        from minio import Minio

        self.settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.settings.minio_bucket):
            self.client.make_bucket(self.settings.minio_bucket)

    def upload_crop(self, image: np.ndarray, camera_id: str, event_id: str) -> str:
        ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("Failed to encode crop JPEG")
        data = io.BytesIO(buf.tobytes())
        key = f"{camera_id}/{event_id}.jpg"
        self.client.put_object(
            self.settings.minio_bucket,
            key,
            data,
            length=len(buf),
            content_type="image/jpeg",
        )
        return key


class EventPublisher:
    def __init__(self, settings: Settings, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self._producer: Any = None

    def connect(self) -> None:
        if self.dry_run:
            return
        try:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=self.settings.kafka_bootstrap,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
        except (OSError, ImportError, ValueError) as exc:
            logger.warning("Kafka unavailable (%s); falling back to stdout", exc)
            self._producer = None

    def publish(self, read: PlateRead) -> None:
        payload = read.model_dump(mode="json")
        if self.dry_run:
            print(json.dumps(payload, indent=2))
            return
        if self._producer:
            self._producer.send(self.settings.topic_reads, key=read.plate_norm, value=payload)
        else:
            print(json.dumps(payload))

    def close(self) -> None:
        if self._producer:
            self._producer.flush()
            self._producer.close()


def build_recognizer(settings: Settings, use_mock: bool = False) -> Recognizer:
    if use_mock:
        return MockRecognizer()
    if settings.parseq_weights:
        return ParseqRecognizer(weights_path=settings.parseq_weights)
    return ParseqRecognizer()


class OCRPipeline:
    FUSION_MIN_FRAMES = 3

    def __init__(
        self,
        settings: Settings,
        camera_id: str,
        camera_heading_deg: float = 0.0,
        dry_run: bool = False,
        recognizer: Recognizer | None = None,
    ) -> None:
        self.settings = settings
        self.camera_id = camera_id
        self.camera_heading_deg = camera_heading_deg
        self.dry_run = dry_run
        weights = validate_plate_weights(settings.plate_det_weights)
        self.vehicle_detector = VehicleDetector()
        self.plate_detector = PlateDetector(weights)
        self.recognizer = recognizer or build_recognizer(settings)
        self.enhancer = NoopEnhancer()
        self.uploader = MinioUploader(settings) if not dry_run else None
        self.publisher = EventPublisher(settings, dry_run=dry_run)
        self.tracks: dict[int, TrackState] = {}
        self._active_ids: set[int] = set()

    def run(self, source: str, max_frames: int | None = None) -> int:
        self.publisher.connect()
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video source: {source}")

        emitted = 0
        frame_idx = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                frame_idx += 1
                if max_frames and frame_idx > max_frames:
                    break
                self._process_frame(frame)
                emitted += self._finalize_stale_tracks()
        finally:
            emitted += self._finalize_all_tracks()
            cap.release()
            self.publisher.close()
        return emitted

    def _process_frame(self, frame: np.ndarray) -> None:
        vehicles = self.vehicle_detector.track(frame)
        current_ids: set[int] = set()
        for veh in vehicles:
            if veh.track_id is None:
                continue
            tid = veh.track_id
            current_ids.add(tid)
            state = self.tracks.get(tid)
            if state is None:
                state = TrackState(track_id=tid, vehicle_class=veh.vehicle_class)
                self.tracks[tid] = state
            state.vehicle_class = veh.vehicle_class
            state.frames_seen += 1
            x1, y1, x2, y2 = veh.bbox
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            state.centers.append((cx, cy))

            h, w = frame.shape[:2]
            pad = 10
            vx1 = max(0, int(x1) - pad)
            vy1 = max(0, int(y1) - pad)
            vx2 = min(w, int(x2) + pad)
            vy2 = min(h, int(y2) + pad)
            crop = frame[vy1:vy2, vx1:vx2]
            if crop.size == 0:
                continue

            plates = self.plate_detector.detect(crop)
            if not plates:
                continue
            best = max(plates, key=lambda p: p.confidence)
            corners = best.corners.copy()
            corners[:, 0] += vx1
            corners[:, 1] += vy1
            two_row = is_two_row_plate(corners)
            rectified = rectify_plate(frame, corners, two_row=two_row)
            pixel_h = float(np.linalg.norm(corners[3] - corners[0]))
            quality = compute_quality(rectified, pixel_h)
            rec = self.recognizer.recognize(
                enhance_plate(rectified, quality, None, self.enhancer)
            )
            rec2 = self.recognizer.recognize(
                enhance_plate(rectified, quality, rec.confidence, self.enhancer)
            )
            state.frame_reads.append(
                FrameRead(text=rec2.text, char_probs=rec2.char_probs, quality=quality)
            )
            if quality > state.best_quality:
                state.best_quality = quality
                state.best_crop = rectified.copy()

        self._active_ids = current_ids

    def _finalize_stale_tracks(self) -> int:
        emitted = 0
        for tid, state in list(self.tracks.items()):
            if tid in self._active_ids:
                continue
            if (
                state.frames_seen >= self.FUSION_MIN_FRAMES
                and state.frame_reads
                and self._emit_track(state)
            ):
                emitted += 1
            del self.tracks[tid]
        return emitted

    def _finalize_all_tracks(self) -> int:
        emitted = 0
        for state in self.tracks.values():
            if (
                state.frames_seen >= self.FUSION_MIN_FRAMES
                and state.frame_reads
                and self._emit_track(state)
            ):
                emitted += 1
        self.tracks.clear()
        return emitted

    def _emit_track(self, state: TrackState) -> bool:
        fused = fuse_track_reads(state.frame_reads)
        if not fused.text or fused.text == "UNKNOWN":
            return False

        grammar = normalize_plate(fused.text)
        direction = estimate_direction(state.centers, self.camera_heading_deg)
        event_id = uuid4()
        crop_key = None
        if state.best_crop is not None and self.uploader is not None:
            try:
                crop_key = self.uploader.upload_crop(
                    state.best_crop, self.camera_id, str(event_id)
                )
            except (OSError, ValueError) as exc:
                logger.warning("MinIO upload failed: %s", exc)

        read = PlateRead(
            event_id=event_id,
            camera_id=self.camera_id,
            ts=datetime.now(UTC),
            plate_raw=fused.text,
            plate_norm=grammar.norm,
            plate_valid=grammar.valid,
            plate_format=PlateFormat(grammar.format),
            confidence=round(fused.confidence, 3),
            char_conf=fused.char_conf,
            alternates=fused.alternates,
            direction=direction,
            vehicle_class=VEHICLE_CLASS_MAP.get(state.vehicle_class, VehicleClass.car),
            crop_key=crop_key,
            source="ocr",
        )
        self.publisher.publish(read)
        return True
