"""Tests for Indian plate-format constrained decoding."""

import numpy as np
from ocr_engine.plate_format import INDIA_STATE_CODES, decode_india


def _one_hot_probs(text: str, alphabet: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_", slots: int = 10):
    """Build near-one-hot slot probabilities that favour ``text`` then pad."""
    probs = np.full((slots, len(alphabet)), 1e-4, dtype=np.float64)
    pad = alphabet.index("_")
    for i, ch in enumerate(text):
        probs[i, alphabet.index(ch)] = 0.95
    for i in range(len(text), slots):
        probs[i, pad] = 0.95
    probs /= probs.sum(axis=1, keepdims=True)
    return probs


def test_decode_india_standard_plate():
    text, confs = decode_india(_one_hot_probs("MH12AB1234"), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "_")
    assert text == "MH12AB1234"
    assert len(confs) == len(text)
    assert all(c > 0.5 for c in confs)


def test_decode_india_bh_series():
    text, _ = decode_india(_one_hot_probs("22BH1234AA"), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_", "_")
    assert text == "22BH1234AA"


def test_decode_india_prefers_valid_state_over_garbage():
    # Slot 0–1 slightly prefer invalid "KH", but MH is close — decoder must pick a real state.
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
    probs = _one_hot_probs("MH12AB1234", alphabet)
    # Boost K a bit on slot 0 so argmax alone might pick KH — format decoder still picks MH.
    probs[0, alphabet.index("K")] = 0.4
    probs[0, alphabet.index("M")] = 0.55
    probs[0] /= probs[0].sum()
    text, _ = decode_india(probs, alphabet, "_")
    assert text[:2] in INDIA_STATE_CODES
    assert text.endswith("1234")


def test_tg_in_state_codes():
    assert "TG" in INDIA_STATE_CODES
    assert "TS" in INDIA_STATE_CODES
