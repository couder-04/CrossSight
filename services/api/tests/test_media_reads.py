"""Uploaded video frames become plate reads through the existing recognizer interface."""

from datetime import UTC, datetime

from api.media import reads_from_frames


def test_reads_from_frames_skip_unknown_and_keep_source():
    frames = ["a", "b", "c"]

    def recognize(frame):
        if frame == "b":
            return {"plate": "MH12AB1234", "confidence": 0.88}
        return {"plate": "UNKNOWN", "confidence": 0}

    reads = reads_from_frames(
        frames,
        camera_id="cam-9",
        started_at=datetime(2026, 6, 1, tzinfo=UTC),
        source_key="uploads/video/1/clip.mp4",
        fps=10,
        every_n=10,
        recognize=recognize,
    )
    assert len(reads) == 1
    assert reads[0]["plate"] == "MH12AB1234"
    assert reads[0]["camera_id"] == "cam-9"
    assert reads[0]["source_video_key"].endswith("clip.mp4")
    assert reads[0]["confidence"] == 0.88
