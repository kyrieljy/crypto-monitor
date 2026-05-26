from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeSettings:
    database_path: Path
    app_secret_key: str
    admin_password: str
    host: str
    port: int
    log_level: str
    request_timeout_seconds: int
    run_workers: bool


def load_runtime_settings() -> RuntimeSettings:
    return RuntimeSettings(
        database_path=Path(os.getenv("DATABASE_PATH", "./data/market_monitor.db")),
        app_secret_key=os.getenv("APP_SECRET_KEY", "dev-secret-change-this"),
        admin_password=os.getenv("ADMIN_PASSWORD", "change-me-admin"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=_int_env("PORT", 8800),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        request_timeout_seconds=_int_env("REQUEST_TIMEOUT_SECONDS", 20),
        run_workers=_bool_env("RUN_WORKERS", True),
    )
