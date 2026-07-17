from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.app.core.database import Database
from backend.app.services.events import EventBus
from backend.app.services.market_data import Candle
from backend.app.services.market_data import OkxSwapDataSource
from backend.app.services.notification_worker import NotificationWorker
from backend.app.services.store import Store
from backend.app.services.strategies import TechnicalStrategyRunner


class FakeMarketRouter:
    def __init__(self, *, live_candle: bool) -> None:
        self.live_candle = live_candle
        self.calls: list[tuple[str, str]] = []

    def fetch_klines(self, symbol: str, interval: str, limit: int, preference: str):
        self.calls.append((symbol, interval))
        closed_prices = [3.0, 1.0, 1.0, 3.0]
        prices = closed_prices if self.live_candle else [*closed_prices, 0.5]
        candles = [
            Candle(
                symbol=symbol,
                open_time_ms=index + 1,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=1.0,
                source="fake",
            )
            for index, price in enumerate(prices)
        ]
        return candles, "fake", "PRIMARY"


def enable_only(store: Store, *symbols: str) -> None:
    store.db.execute("UPDATE symbols SET enabled = 0")
    for symbol in symbols:
        store.db.execute("UPDATE symbols SET enabled = 1 WHERE symbol = ?", (symbol,))


@pytest.mark.parametrize("alert_on_live_candle", [False, True])
def test_boll_ma_cross_keeps_dashboard_alerts_but_filters_notifications(tmp_path: Path, alert_on_live_candle: bool) -> None:
    store = Store(Database(tmp_path / f"test-{alert_on_live_candle}.db", "secret"))
    enable_only(store, "BTCUSDT", "ETHUSDT")
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    store.update_strategy(
        "boll_ma_cross",
        True,
        {
            **strategy.config,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "notify_intervals_by_symbol": {"BTCUSDT": ["1h"]},
            "boll_period": 2,
            "ma_period": 3,
            "alert_on_live_candle": alert_on_live_candle,
            "candle_limit": 10,
        },
        None,
    )
    runner = TechnicalStrategyRunner(store, FakeMarketRouter(live_candle=alert_on_live_candle), EventBus())  # type: ignore[arg-type]

    runner._run_boll_ma_cross()

    alerts = store.list_alerts(20)
    assert len(alerts) == 8
    assert {alert.symbol for alert in alerts} == {"BTCUSDT", "ETHUSDT"}
    assert all(alert.signal == "BOLL_MIDDLE_CROSS_ABOVE_MA" for alert in alerts)
    assert all("MA3" in alert.message for alert in alerts)
    pending = store.list_pending_alert_notifications()
    assert len(pending) == 1
    assert pending[0]["symbol"] == "BTCUSDT"
    assert pending[0]["interval"] == "1h"


def test_empty_notification_matrix_suppresses_all_notifications_without_hiding_alerts(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    enable_only(store, "BTCUSDT", "ETHUSDT")
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    store.update_strategy(
        "boll_ma_cross",
        True,
        {
            **strategy.config,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "notify_intervals_by_symbol": {},
            "boll_period": 2,
            "ma_period": 3,
            "alert_on_live_candle": True,
        },
        None,
    )
    runner = TechnicalStrategyRunner(store, FakeMarketRouter(live_candle=True), EventBus())  # type: ignore[arg-type]

    runner._run_boll_ma_cross()

    assert len(store.list_alerts(20)) == 8
    assert store.list_pending_alert_notifications() == []


def test_notification_matrix_is_exact_per_symbol_and_fetches_extra_intervals_only_where_selected(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    enable_only(store, "BTCUSDT", "ETHUSDT")
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    store.update_strategy(
        "boll_ma_cross",
        True,
        {
            **strategy.config,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "notify_intervals_by_symbol": {"BTCUSDT": ["30m"], "ETHUSDT": ["1h"]},
            "boll_period": 2,
            "ma_period": 3,
            "alert_on_live_candle": True,
        },
        None,
    )
    market_router = FakeMarketRouter(live_candle=True)
    runner = TechnicalStrategyRunner(store, market_router, EventBus())  # type: ignore[arg-type]

    runner._run_boll_ma_cross()

    pending = store.list_pending_alert_notifications()
    assert {(row["symbol"], row["interval"]) for row in pending} == {("BTCUSDT", "30m"), ("ETHUSDT", "1h")}
    assert ("BTCUSDT", "30m") in market_router.calls
    assert ("ETHUSDT", "30m") not in market_router.calls
    assert len(store.list_alerts(20)) == 9


def test_global_enabled_symbol_is_monitored_even_when_missing_from_legacy_symbols_config(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    enable_only(store, "BTCUSDT", "ETHUSDT")
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    store.update_strategy(
        "boll_ma_cross",
        True,
        {
            **strategy.config,
            "symbols": ["BTCUSDT"],
            "notify_intervals_by_symbol": {"ETHUSDT": ["1h"]},
            "boll_period": 2,
            "ma_period": 3,
            "alert_on_live_candle": True,
        },
        None,
    )

    TechnicalStrategyRunner(store, FakeMarketRouter(live_candle=True), EventBus())._run_boll_ma_cross()  # type: ignore[arg-type]

    alerts = store.list_alerts(20)
    assert {alert.symbol for alert in alerts} == {"BTCUSDT", "ETHUSDT"}
    pending = store.list_pending_alert_notifications()
    assert [(row["symbol"], row["interval"]) for row in pending] == [("ETHUSDT", "1h")]


class FakeNotificationService:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_strategy_message(self, strategy_id: str, message: str) -> tuple[bool, bool, str]:
        self.messages.append((strategy_id, message))
        return True, False, "ok"


def test_notification_worker_skips_technical_event_removed_from_latest_matrix(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    enable_only(store, "BTCUSDT")
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    config: dict[str, Any] = {
        **strategy.config,
        "symbols": ["BTCUSDT"],
        "notify_intervals_by_symbol": {"BTCUSDT": ["1h"]},
        "boll_period": 2,
        "ma_period": 3,
        "alert_on_live_candle": True,
    }
    store.update_strategy("boll_ma_cross", True, config, None)
    TechnicalStrategyRunner(store, FakeMarketRouter(live_candle=True), EventBus())._run_boll_ma_cross()  # type: ignore[arg-type]
    assert len(store.list_pending_alert_notifications()) == 1

    store.update_strategy("boll_ma_cross", True, {**config, "notify_intervals_by_symbol": {}}, None)
    notification_service = FakeNotificationService()
    NotificationWorker(store, notification_service).run_once()  # type: ignore[arg-type]

    assert notification_service.messages == []
    assert store.list_pending_alert_notifications() == []


def test_okx_supports_all_matrix_intervals_including_30m() -> None:
    assert [OkxSwapDataSource._map_interval(interval) for interval in ("1m", "5m", "15m", "30m", "1h", "4h", "1d")] == [
        "1m", "5m", "15m", "30m", "1H", "4H", "1D"
    ]
