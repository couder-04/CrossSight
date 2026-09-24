"""OCR engine CLI."""

from __future__ import annotations

import logging
import sys

import click
from anpr_common.config import get_settings

from ocr_engine.pipeline import OCRPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@click.group()
def main() -> None:
    """ANPR OCR engine."""


@main.command("run")
@click.option("--source", required=True, help="Video file path or RTSP URL")
@click.option("--camera-id", required=True, help="Camera identifier for PlateRead events")
@click.option("--camera-heading", type=float, default=0.0, help="Camera compass heading in degrees")
@click.option("--dry-run", is_flag=True, help="Print events instead of publishing to Kafka/MinIO")
@click.option("--max-frames", type=int, default=None, help="Stop after N frames (debug)")
def run_cmd(
    source: str,
    camera_id: str,
    camera_heading: float,
    dry_run: bool,
    max_frames: int | None,
) -> None:
    """Run the video OCR pipeline on a file or RTSP stream."""
    settings = get_settings()
    pipeline = OCRPipeline(
        settings=settings,
        camera_id=camera_id,
        camera_heading_deg=camera_heading,
        dry_run=dry_run,
    )
    try:
        count = pipeline.run(source, max_frames=max_frames)
        click.echo(f"Emitted {count} plate read(s)")
    except KeyboardInterrupt:
        click.echo("Interrupted", err=True)
        sys.exit(130)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        sys.exit(code)
    except (RuntimeError, OSError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
