from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import Store


LOGGER = logging.getLogger("market_monitor.cleanup")
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "schedule_time": "12:30",
    "timezone": "Asia/Shanghai",
    "alert_retention_days": 30,
    "news_retention_days": 60,
    "whale_retention_days": 90,
    "btc_candidate_retention_days": 90,
    "delete_pending_notifications": True,
    "vacuum_after_cleanup": True,
}


class CleanupWorker:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                LOGGER.exception("定时清理轮询失败")
            await asyncio.sleep(60)

    def run_once(self, now: datetime | None = None) -> dict[str, int | bool | str] | None:
        strategy = self.store.get_strategy("cleanup")
        if strategy is None or not strategy.enabled:
            return None
        config = {**DEFAULT_CONFIG, **strategy.config}
        if not bool(config.get("enabled", True)):
            return None

        current = now or datetime.now(timezone.utc)
        local_now = _local_time(current, str(config.get("timezone") or DEFAULT_CONFIG["timezone"]))
        run_date = local_now.date().isoformat()
        if self.store.state_get("cleanup_last_run_date") == run_date:
            return None
        if local_now.strftime("%H:%M") < _schedule_time(str(config.get("schedule_time") or "12:30")):
            return None

        result = self.store.cleanup_old_data(
            alert_retention_days=_positive_int(config.get("alert_retention_days"), 30),
            news_retention_days=_positive_int(config.get("news_retention_days"), 60),
            whale_retention_days=_positive_int(config.get("whale_retention_days"), 90),
            delete_pending_notifications=bool(config.get("delete_pending_notifications", True)),
            vacuum_after_cleanup=bool(config.get("vacuum_after_cleanup", True)),
            now=current,
        )
        whale_strategy = self.store.get_strategy("whale")
        whale_config = whale_strategy.config if whale_strategy is not None else {}
        btc_deleted = self.store.cleanup_btc_large_transfers(
            _positive_int(whale_config.get("btc_candidate_retention_days", config.get("btc_candidate_retention_days")), 90),
            now=current,
        )
        result["btc_large_transfer_deleted"] = btc_deleted
        result["total_deleted"] = int(result.get("total_deleted", 0)) + btc_deleted
        self.store.state_set("cleanup_last_run_date", run_date)
        self.store.state_set("cleanup_last_run_at", local_now.isoformat())
        LOGGER.info("定时清理完成 result=%s", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result


def _local_time(value: datetime, timezone_name: str) -> datetime:
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=8))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz)


def _schedule_time(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"\d{2}:\d{2}", text):
        hour, minute = text.split(":", 1)
        if 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59:
            return text
    return "12:30"


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(1, parsed)
