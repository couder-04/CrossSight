"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from anpr_common.config import DEFAULT_JWT_SECRET, Settings, get_settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import get_session_factory
from api.migrate import apply_postgres_upgrade
from api.routes import (
    alerts,
    analytics,
    audit,
    auth,
    cameras,
    crops,
    platform,
    trajectory,
    transfers,
    watchlist,
    ws,
    zones,
)
from api.seed import ensure_seed_users

logger = logging.getLogger(__name__)


def _validate_settings(settings: Settings) -> None:
    """Refuse default demo secrets outside local development."""
    if settings.app_env == "dev":
        return
    unsafe: list[str] = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        unsafe.append("JWT_SECRET")
    if settings.postgres_password == "anpr":
        unsafe.append("POSTGRES_PASSWORD")
    if settings.minio_secret_key == "minioadmin":
        unsafe.append("MINIO_SECRET_KEY")
    if unsafe:
        raise RuntimeError(
            "Refusing to boot with default "
            + ", ".join(unsafe)
            + f" while APP_ENV={settings.app_env}"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _validate_settings(settings)
    factory = get_session_factory(settings)
    async with factory() as session:
        await apply_postgres_upgrade(session)
        await ensure_seed_users(session, settings)
    logger.info("API started; seed users ensured")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ANPR Platform API",
        version="0.1.0",
        description="City-wide ANPR intelligence REST and WebSocket API",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(cameras.router)
    app.include_router(zones.router)
    app.include_router(watchlist.router)
    app.include_router(trajectory.router)
    app.include_router(analytics.router)
    app.include_router(alerts.router)
    app.include_router(platform.router)
    app.include_router(transfers.router)
    app.include_router(crops.router)
    app.include_router(audit.router)
    app.include_router(ws.router)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
