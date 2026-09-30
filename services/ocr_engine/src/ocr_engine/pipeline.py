"""Video OCR pipeline: detect → track → read → fuse → publish.

Backends:
  - plateocr (default): FastALPR YOLOv9 + CCT ONNX (https://github.com/Ajitesh-07/PlateOCR)
  - legacy: Ultralytics vehicle/plate YOLO + PARSeq
"""

from __future__ import annotations

import io
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
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
from ocr_engine.enhance import ClassicalDeblurEnhancer, enhance_plate
from ocr_engine.fusion import FrameRead, fuse_track_reads
from ocr_engine.recognize import MockRecognizer, ParseqRecognizer, Recognizer

logger = logging.getLogger(__name__)

VEHICLE_CLASS_MAP = {
    "car": VehicleClass.car,
    "motorcycle": VehicleClass.motorcycle,
    "bus": VehicleClass.bus,
    "truck": VehicleClass.truck,
}

BackendName = Literal["plateocr", "legacy"]


@dataclass
class TrackState:
    track_id: int
    vehicle_class: str = "car"
    centers: list[tuple[float, float]] = field(default_factory=list)
    frame_reads: list[FrameRead] = field(default_factory=list)
    best_crop: np.ndarray | None = None
    best_quality: float = 0.0
    frames_seen: int = 0
    last_bbox: tuple[int, int, int, int] | None = None
    lane: int | None = None
    frame_width: int = 0


def estimate_lane(
    bbox: tuple[int, int, int, int], frame_width: int, num_lanes: int
) -> int | None:
    """Map plate bbox horizontal center to lane 1..N (left → right)."""
    if num_lanes < 1 or frame_width <= 0:
        return None
    cx = (bbox[0] + bbox[2]) / 2.0
    lane = int(cx / frame_width * num_lanes) + 1
    return max(1, min(num_lanes, lane))


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


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _bbox_center(b: tuple[int, int, int, int]) -> tuple[float, float]:
    x1, y1, x2, y2 = b
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


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
    IOU_MATCH_THRESH = 0.3

    def __init__(
        self,
        settings: Settings,
        camera_id: str,
        camera_heading_deg: float = 0.0,
        dry_run: bool = False,
        recognizer: Recognizer | None = None,
        backend: str | None = None,
        num_lanes: int = 3,
    ) -> None:
        self.settings = settings
        self.camera_id = camera_id
        self.camera_heading_deg = camera_heading_deg
        self.dry_run = dry_run
        self.num_lanes = max(1, num_lanes)
        chosen = (backend or settings.ocr_backend or "plateocr").lower()
        if chosen not in ("plateocr", "legacy"):
            raise ValueError(f"Unknown OCR backend: {chosen}")
        self.backend: BackendName = chosen  # type: ignore[assignment]

        self.vehicle_detector: VehicleDetector | None = None
        self.plate_detector: PlateDetector | None = None
        self.plateocr_reader: Any = None
        self.recognizer: Recognizer | None = recognizer
        self.enhancer = ClassicalDeblurEnhancer()
        self.uploader = MinioUploader(settings) if not dry_run else None
        self.publisher = EventPublisher(settings, dry_run=dry_run)
        self.tracks: dict[int, TrackState] = {}
        self._active_ids: set[int] = set()
        self._next_track_id = 1

        if self.backend == "plateocr":
            from ocr_engine.plateocr_backend import PlateOCRReader, ensure_plateocr_available

            ensure_plateocr_available()
            self.plateocr_reader = PlateOCRReader(
                detector_model=settings.plateocr_detector,
                ocr_model=settings.plateocr_ocr_model,
                device=settings.plateocr_device,
                det_conf=settings.plateocr_det_conf,
                min_ocr_conf=settings.plateocr_min_ocr_conf,
                ocr_config=settings.plateocr_ocr_config or None,
                plate_format=settings.plateocr_plate_format or None,
            )
            logger.info(
                "OCR pipeline backend=plateocr (det=%s ocr=%s format=%s)",
                settings.plateocr_detector,
                settings.plateocr_ocr_model,
                settings.plateocr_plate_format or "auto",
            )
        else:
            weights = validate_plate_weights(settings.plate_det_weights)
            self.vehicle_detector = VehicleDetector()
            self.plate_detector = PlateDetector(weights)
            if self.recognizer is None:
                self.recognizer = build_recognizer(settings)
            logger.info("OCR pipeline backend=legacy (YOLO + PARSeq)")

    def run(
        self,
        source: str,
        max_frames: int | None = None,
        reconnect: bool | None = None,
        reconnect_delay: float = 2.0,
    ) -> int:
        """Process a video file or RTSP stream.

        RTSP sources reconnect by default; file sources do not.
        """
        self.publisher.connect()
        is_stream = str(source).lower().startswith(("rtsp://", "http://", "https://"))
        do_reconnect = is_stream if reconnect is None else reconnect
        emitted = 0
        frame_idx = 0
        try:
            while True:
                cap = cv2.VideoCapture(source)
                if not cap.isOpened():
                    if not do_reconnect:
                        raise RuntimeError(f"Cannot open video source: {source}")
                    logger.warning("Cannot open %s; retrying in %.1fs", source, reconnect_delay)
                    import time

                    time.sleep(reconnect_delay)
                    continue
                # Prefer TCP for RTSP stability when OpenCV/FFmpeg supports it.
                if is_stream:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
                emptied = False
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        emptied = True
                        break
                    frame_idx += 1
                    if max_frames and frame_idx > max_frames:
                        emptied = False
                        do_reconnect = False
                        break
                    if self.backend == "plateocr":
                        self._process_frame_plateocr(frame)
                    else:
                        self._process_frame_legacy(frame)
                    emitted += self._finalize_stale_tracks()
                cap.release()
                if max_frames and frame_idx >= max_frames:
                    break
                if not do_reconnect or not emptied:
                    break
                logger.warning("Stream ended for %s; reconnecting in %.1fs", source, reconnect_delay)
                import time

                time.sleep(reconnect_delay)
        finally:
            emitted += self._finalize_all_tracks()
            self.publisher.close()
        return emitted

    def run_image(self, source: str) -> int:
        """Single-image ALPR (plateocr backend). Emits one event per plate found."""
        if self.backend != "plateocr" or self.plateocr_reader is None:
            raise RuntimeError("run_image requires OCR_BACKEND=plateocr")
        self.publisher.connect()
        img = cv2.imread(source)
        if img is None:
            raise RuntimeError(f"Cannot read image: {source}")
        hits = self.plateocr_reader.read(img)
        emitted = 0
        try:
            for hit in hits:
                x1, y1, x2, y2 = hit.bbox
                crop = img[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)]
                state = TrackState(
                    track_id=self._next_track_id,
                    centers=[_bbox_center(hit.bbox)],
                    frame_reads=[
                        FrameRead(
                            text=hit.text,
                            char_probs=hit.char_probs
                            or [hit.ocr_confidence] * max(1, len(hit.text)),
                            quality=hit.det_confidence,
                        )
                    ],
                    best_crop=crop.copy() if crop.size else None,
                    best_quality=hit.det_confidence,
                    frames_seen=self.FUSION_MIN_FRAMES,
                    last_bbox=hit.bbox,
                )
                self._next_track_id += 1
                if self._emit_track(state):
                    emitted += 1
        finally:
            self.publisher.close()
        return emitted

    def _process_frame_plateocr(self, frame: np.ndarray) -> None:
        assert self.plateocr_reader is not None
        hits = self.plateocr_reader.read(frame)
        current_ids: set[int] = set()
        unmatched = set(self.tracks.keys())
        frame_h, frame_w = frame.shape[:2]

        for hit in hits:
            best_tid: int | None = None
            best_iou = 0.0
            for tid in list(unmatched):
                prev = self.tracks[tid].last_bbox
                if prev is None:
                    continue
                iou = _bbox_iou(hit.bbox, prev)
                if iou > best_iou:
                    best_iou = iou
                    best_tid = tid

            if best_tid is not None and best_iou >= self.IOU_MATCH_THRESH:
                state = self.tracks[best_tid]
                unmatched.discard(best_tid)
            else:
                tid = self._next_track_id
                self._next_track_id += 1
                state = TrackState(track_id=tid)
                self.tracks[tid] = state
                best_tid = tid

            current_ids.add(best_tid)
            state.frames_seen += 1
            state.last_bbox = hit.bbox
            state.frame_width = frame_w
            state.centers.append(_bbox_center(hit.bbox))
            state.lane = estimate_lane(hit.bbox, frame_w, self.num_lanes)
            probs = hit.char_probs or [hit.ocr_confidence] * max(1, len(hit.text))
            quality = float(hit.det_confidence)
            x1, y1, x2, y2 = hit.bbox
            crop = frame[max(0, y1) : min(frame_h, y2), max(0, x1) : min(frame_w, x2)]
            enhanced_text = hit.text
            if crop.size:
                enhanced = enhance_plate(crop, quality, hit.ocr_confidence, self.enhancer)
                # Re-OCR enhanced crop only when gate fired (quality/conf low) via enhancer path.
                # PlateOCR already OCR'd the raw crop; keep text but store enhanced crop for upload.
                if quality < 0.45 or hit.ocr_confidence < 0.65:
                    try:
                        re_text, re_conf, re_probs = self.plateocr_reader.read_crop(enhanced)
                        if re_text and re_conf >= hit.ocr_confidence:
                            enhanced_text = re_text
                            probs = re_probs or [re_conf] * max(1, len(re_text))
                            quality = max(quality, float(re_conf))
                    except Exception:
                        logger.debug("PlateOCR re-read after enhance failed", exc_info=True)
                if quality >= state.best_quality:
                    state.best_quality = quality
                    state.best_crop = enhanced.copy()
            state.frame_reads.append(
                FrameRead(text=enhanced_text, char_probs=probs, quality=quality)
            )

        self._active_ids = current_ids

    def _process_frame_legacy(self, frame: np.ndarray) -> None:
        assert self.vehicle_detector is not None
        assert self.plate_detector is not None
        assert self.recognizer is not None

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
            state.frame_width = w
            state.lane = estimate_lane(veh.bbox, w, self.num_lanes)

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
            lane=state.lane,
            direction=direction,
            vehicle_class=VEHICLE_CLASS_MAP.get(state.vehicle_class, VehicleClass.car),
            crop_key=crop_key,
            source="ocr",
        )
        self.publisher.publish(read)
        return True
