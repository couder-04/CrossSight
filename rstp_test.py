"""Standalone RTSP smoke test — prefer the fleet CLI for production OCR.

  uv run python -m ocr_engine.cli run --source "$RTSP_URL" --camera-id cam-rtsp-1 --dry-run
  uv run python -m ocr_engine.cli fleet --config services/ocr_engine/config/cameras.example.json --dry-run
"""

from __future__ import annotations

import os
import sys

import cv2

RTSP_URL = os.environ.get(
    "RTSP_URL",
    "rtsp://rtsp.rtsplink.com/live/traffic?token=REPLACE_ME",
)


def main() -> int:
    cap = cv2.VideoCapture(RTSP_URL)
    if not cap.isOpened():
        print(f"Cannot open RTSP source: {RTSP_URL}", file=sys.stderr)
        return 1
    print("Streaming… press q to quit")
    while cap.isOpened():
        ok, frame = cap.read()
        if not ok:
            break
        cv2.imshow("RTSP smoke test", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
