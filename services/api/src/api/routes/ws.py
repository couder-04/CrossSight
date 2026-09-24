"""WebSocket live feed from Redis pub/sub."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.deps import get_redis, get_settings_dep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

ALLOWED_CHANNELS = frozenset({"heatmap", "alerts", "flow"})


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    settings = get_settings_dep()
    redis = await get_redis(settings)
    pubsub = redis.pubsub()
    subscribed: set[str] = set()

    async def reader() -> None:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                channel = message.get("channel")
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                payload: dict[str, Any]
                try:
                    payload = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    payload = {"raw": data}
                await websocket.send_json({"channel": channel, "data": payload})
            await asyncio.sleep(0.01)

    reader_task: asyncio.Task | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "invalid_json"})
                continue

            if "subscribe" in msg:
                channels = msg["subscribe"]
                if not isinstance(channels, list):
                    await websocket.send_json({"error": "subscribe must be a list"})
                    continue
                for ch in channels:
                    if ch not in ALLOWED_CHANNELS:
                        await websocket.send_json({"error": f"unknown channel: {ch}"})
                        continue
                    if ch not in subscribed:
                        await pubsub.subscribe(ch)
                        subscribed.add(ch)
                if reader_task is None and subscribed:
                    reader_task = asyncio.create_task(reader())
                await websocket.send_json({"subscribed": sorted(subscribed)})

            elif "unsubscribe" in msg:
                for ch in msg["unsubscribe"]:
                    if ch in subscribed:
                        await pubsub.unsubscribe(ch)
                        subscribed.discard(ch)
                await websocket.send_json({"subscribed": sorted(subscribed)})

            elif msg.get("ping"):
                await websocket.send_json({"pong": True})

    except WebSocketDisconnect:
        pass
    finally:
        if reader_task is not None:
            reader_task.cancel()
        if subscribed:
            await pubsub.unsubscribe(*subscribed)
        await pubsub.aclose()
