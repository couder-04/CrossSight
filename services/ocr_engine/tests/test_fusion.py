"""Unit tests for track-level fusion."""

from ocr_engine.fusion import FrameRead, fuse_track_reads


def test_fusion_single_read():
    reads = [FrameRead(text="MH12AB1234", char_probs=[0.9] * 10, quality=0.8)]
    result = fuse_track_reads(reads)
    assert result.text == "MH12AB1234"
    assert len(result.char_conf) == 10
    assert result.confidence > 0


def test_fusion_majority_vote():
    reads = [
        FrameRead(text="MH12AB1234", char_probs=[0.95] * 10, quality=0.9),
        FrameRead(text="MH12AB1235", char_probs=[0.6] * 10, quality=0.5),
        FrameRead(text="MH12AB1234", char_probs=[0.92] * 10, quality=0.85),
    ]
    result = fuse_track_reads(reads)
    assert result.text == "MH12AB1234"
    assert result.confidence > 0.5


def test_fusion_empty():
    result = fuse_track_reads([])
    assert result.text == ""
    assert result.confidence == 0.0


def test_fusion_alternates():
    reads = [
        FrameRead(text="DL1CA1234", char_probs=[0.9] * 9, quality=0.8),
        FrameRead(text="DL1CB1234", char_probs=[0.7] * 9, quality=0.7),
    ]
    result = fuse_track_reads(reads, top_k=2)
    assert result.text
    assert isinstance(result.alternates, list)
