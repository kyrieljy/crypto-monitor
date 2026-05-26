from __future__ import annotations

import uvicorn

from .core.settings import load_runtime_settings


settings = load_runtime_settings()

uvicorn.run(
    "backend.app.main:app",
    host=settings.host,
    port=settings.port,
    reload=False,
    log_level=settings.log_level.lower(),
)
