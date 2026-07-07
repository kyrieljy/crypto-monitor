from __future__ import annotations

import threading
import time
from urllib.parse import urlparse


_LOCK = threading.Lock()
_LAST_REQUEST_BY_HOST: dict[str, float] = {}


def wait_for_host_rate_limit(base_url: str, min_interval_seconds: float) -> None:
    interval = max(0.0, float(min_interval_seconds))
    host = urlparse(base_url).netloc or base_url
    if interval <= 0 or not host:
        return
    with _LOCK:
        now = time.monotonic()
        elapsed = now - _LAST_REQUEST_BY_HOST.get(host, 0.0)
        if elapsed < interval and _LAST_REQUEST_BY_HOST.get(host, 0.0) > 0:
            time.sleep(interval - elapsed)
            now = time.monotonic()
        _LAST_REQUEST_BY_HOST[host] = now


def reset_host_rate_limits_for_tests() -> None:
    with _LOCK:
        _LAST_REQUEST_BY_HOST.clear()
