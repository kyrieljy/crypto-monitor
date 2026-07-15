from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.database import Database
from backend.app.services.events import EventBus
from backend.app.services.market_data import Candle
from backend.app.services.store import Store
from backend.app.services.strategies import TechnicalStrategyRunner


class FakeMarketRouter:
    def __init__(self, *, live_candle: bool) -> None:
        self.live_candle = live_candle

    def fetch_klines(self, symbol: str, interval: str, limit: int, preference: str):
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


@pytest.mark.parametrize("alert_on_live_candle", [False, True])
def test_boll_ma_cross_keeps_dashboard_alerts_but_filters_notifications(tmp_path: Path, alert_on_live_candle: bool) -> None:
    store = Store(Database(tmp_path / f"test-{alert_on_live_candle}.db", "secret"))
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    store.update_strategy(
        "boll_ma_cross",
        True,
        {
            **strategy.config,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "notify_symbols": ["BTCUSDT"],
            "intervals": ["1h"],
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


def test_empty_notify_symbols_suppresses_all_notifications_without_hiding_alerts(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    store.update_strategy(
        "boll_ma_cross",
        True,
        {
            **strategy.config,
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "notify_symbols": [],
            "intervals": ["1h"],
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


def test_notify_symbol_scope_distinguishes_empty_and_legacy_configs(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    runner = TechnicalStrategyRunner(store, FakeMarketRouter(live_candle=True), EventBus())  # type: ignore[arg-type]

    assert runner._notify_symbols_for({"symbols": ["BTCUSDT", "ETHUSDT"], "notify_symbols": []}) == set()
    assert runner._notify_symbols_for({"symbols": ["BTCUSDT", "ETHUSDT"]}) == {"BTCUSDT", "ETHUSDT"}
    assert runner._notify_symbols_for({}) == set(store.enabled_symbols())
