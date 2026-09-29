"""PlateOCR / FastALPR backend — YOLOv9 detector + CCT OCR via ONNX.

Adapted from https://github.com/Ajitesh-07/PlateOCR (FastALPR stack).
Weights auto-download to user cache on first use (~33 MB).
"""

from __future__ import annotations

import logging
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ocr_engine.recognize import RecognitionResult, Recognizer

logger = logging.getLogger(__name__)

DEFAULT_DETECTOR = "yolo-v9-s-608-license-plate-end2end"
DEFAULT_OCR = "cct-s-v2-global-model"

PLATEOCR_HELP = """
OCR_BACKEND=plateocr requires the fast-alpr package (ONNX Runtime).

Install (CPU):
  uv add --package anpr-ocr-engine "fast-alpr[onnx]==0.4.0"

On first run, detector + OCR ONNX weights (~33 MB) download to:
  ~/.cache/open-image-models/
  ~/.cache/fast-plate-ocr/
"""


@dataclass
class PlateHit:
    text: str
    ocr_confidence: float
    det_confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    region: str | None = None
    char_probs: list[float] | None = None


def _pick_providers(device: str) -> list[str]:
    import onnxruntime as ort

    available = ort.get_available_providers()
    if device == "cpu":
        return ["CPUExecutionProvider"]
    if device == "cuda" and "CUDAExecutionProvider" not in available:
        raise RuntimeError(f"CUDA requested but not available. Providers: {available}")
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    return [p for p in preferred if p in available]


def _normalize_text(raw: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", raw.replace("_", "").upper())


class PlateOCRReader:
    """Full-frame detect+read. Load once per process; call read() per frame."""

    def __init__(
        self,
        detector_model: str = DEFAULT_DETECTOR,
        ocr_model: str = DEFAULT_OCR,
        device: str = "auto",
        det_conf: float = 0.4,
        min_ocr_conf: float = 0.0,
    ) -> None:
        try:
            import onnxruntime as ort
            from fast_alpr import ALPR
        except ImportError as exc:
            logger.error("fast-alpr / onnxruntime missing.%s", PLATEOCR_HELP)
            raise SystemExit(1) from exc

        if device in ("auto", "cuda"):
            try:
                ort.preload_dlls()
            except Exception:  # noqa: BLE001, S110 - optional GPU DLL preload
                pass

        self.min_ocr_conf = min_ocr_conf
        providers = _pick_providers(device)
        self._alpr = self._build(ALPR, detector_model, ocr_model, det_conf, providers)
        try:
            self._warmup()
        except Exception as exc:
            if providers == ["CPUExecutionProvider"] or device == "cuda":
                raise
            logger.warning("GPU warm-up failed, falling back to CPU: %s", str(exc).splitlines()[0])
            providers = ["CPUExecutionProvider"]
            self._alpr = self._build(ALPR, detector_model, ocr_model, det_conf, providers)
            self._warmup()
        self.providers = providers
        self.detector_model = detector_model
        self.ocr_model = ocr_model
        logger.info(
            "PlateOCR ready (det=%s ocr=%s provider=%s)",
            detector_model,
            ocr_model,
            providers[0],
        )

    @staticmethod
    def _build(
        alpr_cls: Any,
        detector_model: str,
        ocr_model: str,
        det_conf: float,
        providers: list[str],
    ) -> Any:
        return alpr_cls(
            detector_model=detector_model,
            detector_conf_thresh=det_conf,
            detector_providers=providers,
            ocr_model=ocr_model,
            ocr_providers=providers,
        )

    def _warmup(self) -> None:
        self._alpr.predict(np.zeros((640, 640, 3), dtype=np.uint8))

    def read(self, image: np.ndarray | str | Path) -> list[PlateHit]:
        import cv2

        if isinstance(image, (str, Path)):
            img = cv2.imread(str(image))
            if img is None:
                raise ValueError(f"Could not read image: {image}")
        else:
            img = image

        hits: list[PlateHit] = []
        for r in self._alpr.predict(img):
            if r.ocr is None or not r.ocr.text:
                continue
            conf = r.ocr.confidence
            if isinstance(conf, list):
                char_probs = [float(c) for c in conf]
                ocr_conf = float(statistics.mean(char_probs)) if char_probs else 0.0
            else:
                ocr_conf = float(conf)
                char_probs = [ocr_conf]
            if ocr_conf < self.min_ocr_conf:
                continue
            text = _normalize_text(r.ocr.text)
            if not text:
                continue
            b = r.detection.bounding_box
            hits.append(
                PlateHit(
                    text=text,
                    ocr_confidence=round(ocr_conf, 4),
                    det_confidence=round(float(r.detection.confidence), 4),
                    bbox=(int(b.x1), int(b.y1), int(b.x2), int(b.y2)),
                    region=getattr(r.ocr, "region", None),
                    char_probs=char_probs,
                )
            )
        return hits


class PlateOCRRecognizer(Recognizer):
    """Recognizer adapter: runs full ALPR on a crop (or whole image)."""

    def __init__(self, reader: PlateOCRReader | None = None, **kwargs: Any) -> None:
        self._reader = reader or PlateOCRReader(**kwargs)

    def recognize(self, image: np.ndarray) -> RecognitionResult:
        hits = self._reader.read(image)
        if not hits:
            return RecognitionResult(text="UNKNOWN", char_probs=[], confidence=0.0)
        best = max(hits, key=lambda h: h.ocr_confidence)
        probs = best.char_probs or [best.ocr_confidence] * max(1, len(best.text))
        if len(probs) < len(best.text):
            probs = probs + [best.ocr_confidence] * (len(best.text) - len(probs))
        return RecognitionResult(
            text=best.text,
            char_probs=probs[: len(best.text)],
            confidence=best.ocr_confidence,
        )

    def recognize_path(self, image_path: str | Path) -> RecognitionResult:
        import cv2

        img = cv2.imread(str(image_path))
        if img is None:
            return RecognitionResult(text="UNKNOWN", char_probs=[], confidence=0.0)
        return self.recognize(img)


def ensure_plateocr_available() -> None:
    try:
        import fast_alpr  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        logger.error("PlateOCR backend unavailable.%s", PLATEOCR_HELP)
        sys.exit(1)
