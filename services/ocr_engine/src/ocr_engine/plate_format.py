"""Indian plate-format constrained decoding for CCT OCR slot probabilities.

Ported from vendor/PlateOCR/plate_format.py (Ajitesh-07/PlateOCR).
Adds TG alongside TS to match anpr_common.grammar state whitelist.
"""

from __future__ import annotations

import numpy as np

INDIA_STATE_CODES = (
    "AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL "
    "OD OR PB PY RJ SK TG TN TR TS UA UK UP WB"
).split()

DIGITS = "0123456789"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _india_templates(max_slots: int) -> list[list[str]]:
    """Each template is a list of allowed-character sets, one per slot ("S" = state pair)."""
    out: list[list[str]] = []
    for d in (1, 2):
        for n_letters in range(4):
            t = ["S", "S"] + [DIGITS] * d + [LETTERS] * n_letters + [DIGITS] * 4
            if len(t) <= max_slots:
                out.append(t)
    for n_letters in (1, 2):  # Bharat series
        t = [DIGITS, DIGITS, "B", "H"] + [DIGITS] * 4 + [LETTERS] * n_letters
        if len(t) <= max_slots:
            out.append(t)
    return out


def decode_india(probs: np.ndarray, alphabet: str, pad_char: str) -> tuple[str, list[float]]:
    """
    probs: (max_slots, len(alphabet)) softmax output for one plate.
    Returns the most probable valid Indian plate and its per-character probabilities.
    """
    max_slots = probs.shape[0]
    logp = np.log(np.clip(probs, 1e-9, 1.0))
    idx = {c: i for i, c in enumerate(alphabet)}
    pad = idx[pad_char]
    state_pairs = [(idx[s[0]], idx[s[1]]) for s in INDIA_STATE_CODES if s[0] in idx and s[1] in idx]

    best_score, best_ids = -np.inf, None
    for tmpl in _india_templates(max_slots):
        ids: list[int] = []
        score = 0.0
        if tmpl[0] == "S":
            a, b = max(state_pairs, key=lambda p: logp[0, p[0]] + logp[1, p[1]])
            ids += [a, b]
            score += logp[0, a] + logp[1, b]
            rest = tmpl[2:]
        else:
            rest = tmpl
        for slot, allowed in enumerate(rest, start=len(ids)):
            cand = [idx[c] for c in allowed if c in idx]
            j = max(cand, key=lambda k: logp[slot, k])
            ids.append(j)
            score += logp[slot, j]
        score += float(logp[len(ids) :, pad].sum())
        if score > best_score:
            best_score, best_ids = score, ids

    assert best_ids is not None
    text = "".join(alphabet[i] for i in best_ids)
    confs = [float(probs[s, i]) for s, i in enumerate(best_ids)]
    return text, confs
