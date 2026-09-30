"""Image and video processing that reuses PlateOCR. Uploaded files are never executed."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_recognizer: Any = None


def recognize_bgr(image: Any) -> dict[str, Any]:
    """Run the configured PlateOCR recognizer. Raises if weights cannot be loaded."""
    global _recognizer
    if _recognizer is None:
        from ocr_engine.plateocr_backend import PlateOCRRecognizer

        _recognizer = PlateOCRRecognizer()
    result = _recognizer.recognize(image)
    return {
        "plate": result.text,
        "confidence": float(result.confidence),
        "char_probs": [float(value) for value in result.char_probs],
    }


def decode_image(data: bytes) -> Any:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not decode image")
    return image


def sample_video(path: str, *, every_n: int = 15, limit: int = 40) -> tuple[list[Any], float]:
    import cv2

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError("could not open video")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 10.0
    frames: list[Any] = []
    index = 0
    try:
        while len(frames) < limit:
            ok, frame = capture.read()
            if not ok:
                break
            if index % every_n == 0:
                frames.append(frame)
            index += 1
    finally:
        capture.release()
    if not frames:
        raise ValueError("video contained no readable frames")
    return frames, fps


def reads_from_frames(
    frames: list[Any],
    *,
    camera_id: str,
    started_at: datetime,
    source_key: str,
    fps: float,
    every_n: int,
    recognize=recognize_bgr,
) -> list[dict[str, Any]]:
    """Turn sampled frames into plate-read records using an injected recognizer."""
    from datetime import timedelta

    step_s = every_n / fps if fps else 1.0
    reads: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        try:
            found = recognize(frame)
        except Exception as exc:
            logger.warning("frame OCR failed: %s", exc)
            continue
        plate = str(found.get("plate") or "")
        if not plate or plate == "UNKNOWN":
            continue
        reads.append(
            {
                "camera_id": camera_id,
                "ts": (started_at + timedelta(seconds=index * step_s)).isoformat(),
                "plate": plate,
                "confidence": found.get("confidence"),
                "source_video_key": source_key,
                "frame_index": index * every_n,
            }
        )
    return reads
