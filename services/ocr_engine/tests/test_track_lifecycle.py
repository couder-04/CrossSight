"""PlateOCR track lifecycle: one vehicle -> one event, stamped with capture time."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import numpy as np
import pytest
from anpr_common.schemas import PlateRead
from ocr_engine.bytetrack import ByteTracker
from ocr_engine.enhance import ClassicalDeblurEnhancer
from ocr_engine.pipeline import OCRPipeline
from ocr_engine.plateocr_backend import PlateHit

PLATE = "MH12AB1234"


class _StubReader:
    """Detector sees the plate on every frame except those in ``missed``."""

    def __init__(self, missed: set[int], conf: float = 0.95) -> None:
        self.missed = missed
        self.conf = conf
        self.frame = 0
        self.crop_reads = 0

    def read(self, _img):
        self.frame += 1
        if self.frame in self.missed:
            return []
        x = 100 + 8 * self.frame  # plate drifting right
        return [
            PlateHit(PLATE, self.conf, 0.9, (x, 300, x + 120, 330), char_probs=[self.conf] * 10)
        ]

    def read_crop(self, _crop, **_kw):
        self.crop_reads += 1
        return PLATE, self.conf, [self.conf] * 10


class _Publisher:
    def __init__(self) -> None:
        self.events: list[PlateRead] = []

    def publish(self, read: PlateRead) -> None:
        self.events.append(read)


def _pipeline(reader: _StubReader) -> tuple[OCRPipeline, _Publisher]:
    publisher = _Publisher()
    p = object.__new__(OCRPipeline)
    p.backend = "plateocr"
    p.plateocr_reader = reader
    p.enhancer = ClassicalDeblurEnhancer()
    p.publisher = cast(Any, publisher)
    p.uploader = None
    p.tracks = {}
    p._active_ids = set()
    p._alive_ids = set()
    p._frame_ts = None
    p.use_bytetrack = True
    p.num_lanes = 3
    p.camera_heading_deg = 0.0
    p.camera_id = "cam-test"
    p.byte_tracker = ByteTracker(track_thresh=0.35, match_thresh=0.3, track_buffer=45)
    return p, publisher


def _run(p: OCRPipeline, frames: int, t0: datetime) -> None:
    frame = np.zeros((720, 1280, 3), np.uint8)
    for i in range(frames):
        p._frame_ts = t0 + timedelta(seconds=i)
        p._process_frame_plateocr(frame)
        p._finalize_stale_tracks()
    p._finalize_all_tracks()


@pytest.mark.parametrize("missed", [set(), {5}, {4, 5, 6}])
def test_one_vehicle_one_event_despite_missed_detections(missed):
    p, pub = _pipeline(_StubReader(missed))
    _run(p, 10, datetime(2026, 6, 1, tzinfo=UTC))
    assert [e.plate_norm for e in pub.events] == [PLATE]


def test_event_timestamp_is_capture_time_of_last_sighting():
    t0 = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    p, pub = _pipeline(_StubReader(missed={10}))  # last sighting is frame 9 (index 8)
    _run(p, 10, t0)
    assert pub.events[0].ts == t0 + timedelta(seconds=8)


def test_confident_reads_skip_enhanced_reread():
    reader = _StubReader(missed=set(), conf=0.95)
    _run(_pipeline(reader)[0], 5, datetime(2026, 6, 1, tzinfo=UTC))
    assert reader.crop_reads == 0


def test_weak_reads_get_enhanced_reread():
    reader = _StubReader(missed=set(), conf=0.4)
    _run(_pipeline(reader)[0], 5, datetime(2026, 6, 1, tzinfo=UTC))
    assert reader.crop_reads == 5
