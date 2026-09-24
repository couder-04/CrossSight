"""Application settings loaded from environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    city_query: str | None = Field(default="Pune, India", alias="CITY_QUERY")
    city_bbox: str | None = Field(default=None, alias="CITY_BBOX")
    num_cameras: int = Field(default=60, alias="NUM_CAMERAS")
    num_vehicles: int = Field(default=3000, alias="NUM_VEHICLES")
    max_urban_speed_kmh: float = Field(default=120.0, alias="MAX_URBAN_SPEED_KMH")
    trip_gap_min: int = Field(default=30, alias="TRIP_GAP_MIN")
    od_k_anon: int = Field(default=5, alias="OD_K_ANON")
    raw_retention_days: int = Field(default=30, alias="RAW_RETENTION_DAYS")
    h3_heatmap_res: int = Field(default=8, alias="H3_HEATMAP_RES")
    h3_od_res: int = Field(default=7, alias="H3_OD_RES")
    sim_speed: float = Field(default=60.0, alias="SIM_SPEED")

    kafka_bootstrap: str = Field(default="localhost:19092", alias="KAFKA_BOOTSTRAP")
    topic_reads: str = Field(default="anpr.reads.v1", alias="TOPIC_READS")
    topic_alerts: str = Field(default="alerts.v1", alias="TOPIC_ALERTS")
    topic_flow: str = Field(default="analytics.flow.v1", alias="TOPIC_FLOW")

    clickhouse_host: str = Field(default="localhost", alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, alias="CLICKHOUSE_PORT")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")
    clickhouse_db: str = Field(default="anpr", alias="CLICKHOUSE_DB")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5433, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="anpr", alias="POSTGRES_USER")
    postgres_password: str = Field(default="anpr", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="anpr", alias="POSTGRES_DB")

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="anpr-crops", alias="MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")

    jwt_secret: str = Field(default="change-me-demo-jwt-secret-anpr-platform", alias="JWT_SECRET")
    jwt_expire_minutes: int = Field(default=480, alias="JWT_EXPIRE_MINUTES")
    seed_admin_user: str = Field(default="admin", alias="SEED_ADMIN_USER")
    seed_admin_pass: str = Field(default="admin123", alias="SEED_ADMIN_PASS")
    seed_operator_user: str = Field(default="operator", alias="SEED_OPERATOR_USER")
    seed_operator_pass: str = Field(default="operator123", alias="SEED_OPERATOR_PASS")
    seed_analyst_user: str = Field(default="analyst", alias="SEED_ANALYST_USER")
    seed_analyst_pass: str = Field(default="analyst123", alias="SEED_ANALYST_PASS")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")

    plate_det_weights: str = Field(default="", alias="PLATE_DET_WEIGHTS")
    parseq_weights: str = Field(default="", alias="PARSEEQ_WEIGHTS")
    ocr_camera_id: str = Field(default="cam-demo", alias="OCR_CAMERA_ID")

    map_style_url: str = Field(
        default="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        alias="NEXT_PUBLIC_MAP_STYLE_URL",
    )

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
