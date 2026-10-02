from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/qcross.db"
    log_level: str = "INFO"
    lazyportfolio_base_url: str = "https://www.lazyportfolioetf.com"
    save_raw_snapshots: bool = False
    raw_snapshot_dir: Path = Path("data/snapshots/lazyportfolio")
    export_dir: Path = Path("data/exports")
    report_dir: Path = Path("data/reports")
    cache_dir: Path = Path("data/cache/lazyportfolio")
    log_dir: Path = Path("data/logs")
    archive_dir: Path = Path("data/archive")
    http_connect_timeout: float = Field(default=10, gt=0)
    http_read_timeout: float = Field(default=30, gt=0)
    http_max_attempts: int = Field(default=4, ge=1, le=10)
    http_backoff_seconds: float = Field(default=1, ge=0)
    http_max_retry_delay: float = Field(default=60, gt=0)
    lazyportfolio_min_instruments: int = Field(default=51, ge=51)
    lazyportfolio_max_drop_fraction: float = Field(default=0.20, ge=0, lt=1)
    returns_request_interval: float = Field(default=3, ge=1)
    returns_save_raw_snapshots: bool = False
    analytics_workers: int = Field(default=2, ge=1, le=4)
    analytics_timeout_seconds: float = Field(default=300, gt=0, le=1800)
    analytics_max_jobs: int = Field(default=32, ge=1, le=100)
    analytics_retention_seconds: float = Field(default=3600, ge=1, le=86400)


@lru_cache
def get_settings() -> Settings:
    return Settings()
