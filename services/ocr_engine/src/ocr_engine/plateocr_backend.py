"""PlateOCR / FastALPR backend — YOLOv9 detector + CCT OCR via ONNX.

Adapted from https://github.com/Ajitesh-07/PlateOCR (synced 2026-09-30).

Supports:
  - Hub OCR models (cct-s-v2-global-model, …)
  - Published India fine-tune: ``india-v1`` (auto-download + SHA-256 check)
  - Format-constrained decoding via ``plate_format=india``
"""

from __future__ import annotations

import hashlib
import logging
import os
import statistics
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ocr_engine.plate_format import decode_india
from ocr_engine.recognize import RecognitionResult, Recognizer

logger = logging.getLogger(__name__)

DEFAULT_DETECTOR = "yolo-v9-s-608-license-plate-end2end"
DEFAULT_OCR = "india-v1"

RELEASES_URL = "https://github.com/Ajitesh-07/PlateOCR/releases/download"
CUSTOM_OCR_MODELS: dict[str, dict[str, Any]] = {
    "india-v1": {
        "tag": "india-ocr-v1",
        "onnx": (
            "india_ocr_v1.onnx",
            "6fbb878e1bec7e318c9b43d58f4093d5bb7cb21bfe9d7d5fc18047f6c007a6f8",
        ),
        "config": (
            "india_ocr_v1_plate_config.yaml",
            "3f4718541abd7ba9e7fc050cfbda7463b64b9795d8c99689019c5b58a8e65b41",
        ),
        "plate_format": "india",
    },
}

PLATEOCR_HELP = """
OCR_BACKEND=plateocr requires the fast-alpr package (ONNX Runtime).

Install (CPU):
  uv add --package anpr-ocr-engine "fast-alpr[onnx]==0.4.0"

On first run, detector + OCR ONNX weights download to:
  ~/.cache/open-image-models/          (YOLOv9 detector)
  ~/.cache/fast-plate-ocr/             (global CCT models)
  ~/.cache/plate-ocr/india-v1/         (India fine-tune, ~5 MB)
"""


@dataclass
class PlateHit:
    text: str
    ocr_confidence: float
    det_confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    region: str | None = None
    region_confidence: float | None = None
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_custom_model(name: str) -> tuple[Path, Path]:
    """Return local (onnx, plate_config) paths for a CUSTOM_OCR_MODELS entry."""
    spec = CUSTOM_OCR_MODELS[name]
    cache = Path(os.environ.get("PLATE_OCR_CACHE", Path.home() / ".cache" / "plate-ocr")) / name
    base = os.environ.get("PLATE_OCR_MODEL_URL", RELEASES_URL).rstrip("/")
    cache.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fname, sha in (spec["onnx"], spec["config"]):
        dest = cache / fname
        if not (dest.exists() and _sha256(dest) == sha):
            url = f"{base}/{spec['tag']}/{fname}"
            logger.info("Downloading %s", url)
            tmp = dest.with_suffix(dest.suffix + ".part")
            urllib.request.urlretrieve(url, tmp)  # noqa: S310 - pinned model URL
            if _sha256(tmp) != sha:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"Checksum mismatch for {url}; refusing to use it")
            tmp.replace(dest)
        paths.append(dest)
    return paths[0], paths[1]


class FormatOCR:
    """Wraps fast-alpr DefaultOCR; decodes slot probs under Indian plate grammar."""

    DECODERS = {"india": decode_india}

    def __init__(self, inner: Any, plate_format: str) -> None:
        from fast_alpr.base import BaseOCR  # noqa: F401 — type hint only at runtime

        self.inner = inner
        self.decode = self.DECODERS[plate_format]
        self.rec = inner.ocr_model  # fast_plate_ocr LicensePlateRecognizer

    def predict(self, cropped_plate: np.ndarray) -> Any:
        import cv2
        from fast_alpr.base import OcrResult
        from fast_plate_ocr.core.process import preprocess_image
        from fast_plate_ocr.inference.plate_recognizer import _load_image_from_source

        if cropped_plate is None or cropped_plate.size == 0:
            return None
        cfg = self.rec.config
        code = {"grayscale": cv2.COLOR_BGR2GRAY, "rgb": cv2.COLOR_BGR2RGB}[cfg.image_color_mode]
        x = preprocess_image(_load_image_from_source(cv2.cvtColor(cropped_plate, code), cfg))
        out = self.rec.model.run([self.rec.plate_output_name], {"input": x})[0]
        probs = out.reshape(cfg.max_plate_slots, len(cfg.alphabet))
        text, confs = self.decode(probs, cfg.alphabet, cfg.pad_char)
        return OcrResult(text=text, confidence=confs)


_ORT_DTYPES = {
    "tensor(float)": np.float32,
    "tensor(float16)": np.float16,
    "tensor(uint8)": np.uint8,
}


def _find_sessions(obj: object, depth: int = 4, seen: set[int] | None = None) -> list[Any]:
    import onnxruntime as ort

    seen = seen if seen is not None else set()
    if isinstance(obj, ort.InferenceSession):
        return [obj]
    if depth == 0 or id(obj) in seen or not hasattr(obj, "__dict__"):
        return []
    seen.add(id(obj))
    return [s for v in vars(obj).values() for s in _find_sessions(v, depth - 1, seen)]


def _self_test(alpr: Any) -> None:
    """Run every ONNX session on dummy input so provider errors raise (also warms up)."""
    import onnxruntime as ort

    sessions = _find_sessions(alpr)
    if not sessions:
        raise RuntimeError("No ONNX sessions found inside ALPR; fast-alpr internals changed?")
    for sess in sessions:
        feeds = {
            i.name: np.zeros(
                [d if isinstance(d, int) else 1 for d in i.shape],
                _ORT_DTYPES.get(i.type, np.float32),
            )
            for i in sess.get_inputs()
        }
        sess.run(None, feeds)
    alpr.predict(np.zeros((640, 640, 3), dtype=np.uint8))


class PlateOCRReader:
    """Full-frame detect+read. Load once per process; call read() per frame."""

    def __init__(
        self,
        detector_model: str = DEFAULT_DETECTOR,
        ocr_model: str = DEFAULT_OCR,
        device: str = "auto",
        det_conf: float = 0.4,
        min_ocr_conf: float = 0.0,
        ocr_config: str | None = None,
        plate_format: str | None = None,
    ) -> None:
        try:
            import onnxruntime as ort
            from fast_alpr import ALPR
        except ImportError as exc:
            logger.error("fast-alpr / onnxruntime missing.%s", PLATEOCR_HELP)
            raise SystemExit(1) from exc

        if ocr_model in CUSTOM_OCR_MODELS:
            spec = CUSTOM_OCR_MODELS[ocr_model]
            onnx_path, cfg_path = fetch_custom_model(ocr_model)
            ocr_model, ocr_config = str(onnx_path), str(cfg_path)
            plate_format = plate_format or spec["plate_format"]
        if plate_format == "none":
            plate_format = None

        if device in ("auto", "cuda"):
            try:
                ort.preload_dlls()
            except Exception:  # noqa: BLE001, S110 - optional GPU DLL preload
                pass

        self.min_ocr_conf = min_ocr_conf
        self.plate_format = plate_format
        providers = _pick_providers(device)
        self._alpr = self._build(ALPR, detector_model, ocr_model, ocr_config, det_conf, providers)
        try:
            _self_test(self._alpr)
        except Exception as exc:
            if providers == ["CPUExecutionProvider"] or device == "cuda":
                raise
            logger.warning("GPU self-test failed, falling back to CPU: %s", str(exc).splitlines()[0])
            providers = ["CPUExecutionProvider"]
            self._alpr = self._build(ALPR, detector_model, ocr_model, ocr_config, det_conf, providers)
            _self_test(self._alpr)
        if plate_format:
            self._alpr.ocr = FormatOCR(self._alpr.ocr, plate_format)
        self.providers = providers
        self.detector_model = detector_model
        self.ocr_model = ocr_model
        self.ocr_config = ocr_config
        logger.info(
            "PlateOCR ready (det=%s ocr=%s format=%s provider=%s)",
            detector_model,
            Path(ocr_model).name if ocr_model.endswith(".onnx") else ocr_model,
            plate_format or "none",
            providers[0],
        )

    @staticmethod
    def _build(
        alpr_cls: Any,
        detector_model: str,
        ocr_model: str,
        ocr_config: str | None,
        det_conf: float,
        providers: list[str],
    ) -> Any:
        ocr_kwargs: dict[str, Any] = {"ocr_model": ocr_model}
        if ocr_model.endswith(".onnx"):
            model_path = Path(ocr_model)
            if ocr_config is None:
                yamls = list(model_path.parent.glob("*.yaml"))
                if len(yamls) != 1:
                    raise ValueError(
                        f"Pass ocr_config: expected one *.yaml next to {model_path}, found {len(yamls)}"
                    )
                ocr_config = str(yamls[0])
            ocr_kwargs = {
                "ocr_model": None,
                "ocr_model_path": model_path,
                "ocr_config_path": ocr_config,
            }
        return alpr_cls(
            detector_model=detector_model,
            detector_conf_thresh=det_conf,
            detector_providers=providers,
            ocr_providers=providers,
            **ocr_kwargs,
        )

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
            text = r.ocr.text.replace("_", "").strip().upper()
            text = "".join(ch for ch in text if ch.isalnum())
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
                    region_confidence=getattr(r.ocr, "region_confidence", None),
                    char_probs=char_probs,
                )
            )
        return hits

    def read_crop(self, plate_img: np.ndarray) -> tuple[str, float, list[float]]:
        """OCR-only for an already-cropped plate. Returns (text, mean_conf, char_probs)."""
        r = self._alpr.ocr.predict(plate_img)
        if r is None or not r.text:
            return "", 0.0, []
        if isinstance(r.confidence, list):
            char_probs = [float(c) for c in r.confidence]
            conf = float(statistics.mean(char_probs)) if char_probs else 0.0
        else:
            conf = float(r.confidence)
            char_probs = [conf]
        text = r.text.replace("_", "").strip().upper()
        text = "".join(ch for ch in text if ch.isalnum())
        return text, conf, char_probs


class PlateOCRRecognizer(Recognizer):
    """Recognizer adapter: runs full ALPR on a crop (or whole image)."""

    def __init__(self, reader: PlateOCRReader | None = None, **kwargs: Any) -> None:
        self._reader = reader or PlateOCRReader(**kwargs)

    def recognize(self, image: np.ndarray) -> RecognitionResult:
        # Prefer OCR-only on crops (eval fixtures / enhanced plates).
        text, conf, probs = self._reader.read_crop(image)
        if text:
            if len(probs) < len(text):
                probs = probs + [conf] * (len(text) - len(probs))
            return RecognitionResult(
                text=text,
                char_probs=probs[: len(text)],
                confidence=conf,
            )
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
