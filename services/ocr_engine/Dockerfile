FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY packages/anpr_common packages/anpr_common
COPY services/ocr_engine services/ocr_engine

RUN pip install --no-cache-dir uv && \
    uv sync --package anpr-ocr-engine --no-dev

ENV PYTHONPATH=/app/services/ocr_engine/src:/app/packages/anpr_common/src
ENV PLATE_DET_WEIGHTS=/models/plate_det.pt

CMD ["uv", "run", "ocr-engine", "run", "--source", "${OCR_SOURCE:-}", "--camera-id", "${OCR_CAMERA_ID:-cam-demo}"]
