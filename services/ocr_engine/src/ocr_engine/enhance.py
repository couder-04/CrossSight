"""Plate image enhancement: CLAHE always, heavy restoration gated."""

from __future__ import annotations

from abc import ABC, abstractmethod

import cv2
import numpy as np

# Tunable thresholds for heavy enhancement gating.
QUALITY_THRESHOLD = 0.45
CONFIDENCE_THRESHOLD = 0.65


class Enhancer(ABC):
    """Heavy restoration interface (deblur / super-resolution)."""

    @abstractmethod
    def enhance(self, image: np.ndarray) -> np.ndarray:
        """Return restored BGR image."""


class NoopEnhancer(Enhancer):
    """Identity heavy enhancer — used until a real model is wired in."""

    def enhance(self, image: np.ndarray) -> np.ndarray:
        return image


def apply_clahe(image: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    """Apply CLAHE on the L channel in LAB color space."""
    if image.ndim == 2:
        gray = image
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
        return clahe.apply(gray)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    l2 = clahe.apply(l)
    merged = cv2.merge([l2, a, b])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def should_use_heavy_enhancement(quality: float, confidence: float | None) -> bool:
    """Gate heavy restoration on low frame quality or low recognition confidence."""
    return quality < QUALITY_THRESHOLD or (
        confidence is not None and confidence < CONFIDENCE_THRESHOLD
    )


def enhance_plate(
    image: np.ndarray,
    quality: float,
    confidence: float | None,
    enhancer: Enhancer | None = None,
) -> np.ndarray:
    """Always CLAHE; optionally apply heavy enhancer when gated."""
    out = apply_clahe(image)
    if should_use_heavy_enhancement(quality, confidence):
        heavy = enhancer or NoopEnhancer()
        out = heavy.enhance(out)
    return out
