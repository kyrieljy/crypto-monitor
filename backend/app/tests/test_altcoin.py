import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.app.core.database import ALTCOIN_SYMBOLS, DEFAULT_SYMBOLS, Database
from backend.app.core.technical_notifications import BASE_TECHNICAL_STRATEGY_IDS, TECHNICAL_STRATEGY_IDS
from backend.app.services.events import EventBus
from backend.app.services.market_data import Candle, DataSourceError, DataSourceRouter, OKX_SYMBOL_MAP
from backend.app.services.notification_worker import NotificationWorker, format_alert_notification
from backend.app.services.notifiers import NotificationService
from backend.app.services.store import Store
from backend.app.services.strategies import TechnicalStrategyRunner


def test_existing_database_migration_copies_once_and_preserves_layout(tmp_path: Path):
    path = tmp_path / "old.db"
    db = Database(path, "secret")
    store = Store(db)
    original = store.get_strategy("ma")
    store.update_strategy("ma", True, {**original.config, "fast_period": 17}, "feishu-default")
    layout = store.get_layout()
    layout.layout = [item for item in layout.layout if item["i"] != "altcoin_charts"]
    layout.layout[1]["y"] = 123
    store.save_layout(layout)
    db.execute("DELETE FROM strategy_configs WHERE id GLOB 'altcoin_*'")
    db.execute("DELETE FROM symbols WHERE market_group = 'altcoin'")
    db.execute("DELETE FROM app_state WHERE state_key = 'altcoin_symbols_v1'")
    db.close()
    # Simulate the pre-feature schema, not just missing seed rows.
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE symbols DROP COLUMN market_group")
    db = Database(path, "secret")
    store = Store(db)
    assert store.enabled_symbols("main") == DEFAULT_SYMBOLS
    assert store.enabled_symbols("altcoin") == ALTCOIN_SYMBOLS
    new = store.get_strategy("altcoin_ma")
    assert new.config["fast_period"] == 17
    assert new.config["notify_intervals_by_symbol"] == {}
    assert new.config["data_source"] == "binance_then_okx"
    assert new.notifier_id is None
    assert store.get_strategy("ma").notifier_id == "feishu-default"
    assert next(item for item in store.get_layout().layout if item["i"] == layout.layout[1]["i"])["y"] == 123
    store.update_strategy("altcoin_ma", False, {**new.config, "fast_period": 11}, "feishu-default")
    db.execute("UPDATE symbols SET enabled = 0 WHERE symbol = 'TAOUSDT'")
    db.close()
    db = Database(path, "secret")
    store = Store(db)
    assert store.get_strategy("altcoin_ma").config["fast_period"] == 11
    assert not store.get_strategy("altcoin_ma").enabled
    assert store.get_strategy("altcoin_ma").notifier_id == "feishu-default"
    assert "TAOUSDT" not in store.enabled_symbols("altcoin")
    assert len(store.list_symbols()) == 13
    assert len([item for item in store.get_layout().layout if item["i"] == "altcoin_charts"]) == 1
    db.close()


class Market:
    def __init__(self, prices=None, fail_symbol=None):
        self.prices = prices if prices is not None else [3, 1, 1, 3]
        self.calls = []
        self.fail_symbol = fail_symbol

    def fetch_klines(self, symbol, interval, limit, preference):
        self.calls.append((symbol, interval, limit, preference))
        if symbol == self.fail_symbol:
            raise DataSourceError("test source unavailable")
        return [Candle(symbol, index + 1, price, price, price, price, 1, "fake") for index, price in enumerate(self.prices)], "fake", "PRIMARY"


def configure(store, kind):
    store.db.execute("UPDATE symbols SET enabled = 0")
    store.db.execute("UPDATE symbols SET enabled = 1 WHERE symbol IN ('BTCUSDT', 'PENGUUSDT')")
    store.db.execute("UPDATE strategy_configs SET enabled = 0")
    for prefix in ("", "altcoin_"):
        sid = prefix + kind
        strategy = store.get_strategy(sid)
        store.update_strategy(sid, True, {
            **strategy.config, "period": 2, "k_smoothing": 2, "d_smoothing": 2,
            "fast_period": 2, "slow_period": 3, "boll_period": 2, "ma_period": 3, "stddev": 0.5,
            "alert_on_live_candle": True, "candle_limit": 10, "data_source": "binance_then_okx",
            # Deliberate foreign-group entries must never cross the group boundary.
            "notify_intervals_by_symbol": {"BTCUSDT": ["1h"], "PENGUUSDT": ["1h"]},
        }, None)


@pytest.mark.parametrize("kind", BASE_TECHNICAL_STRATEGY_IDS)
def test_all_indicators_group_isolation_dedupe_and_notification_recheck(tmp_path, kind):
    db = Database(tmp_path / "test.db", "secret")
    store = Store(db)
    configure(store, kind)
    runner = TechnicalStrategyRunner(store, Market(), EventBus())
    runner.run_once()
    alerts = store.list_alerts(100)
    assert len(alerts) == 14
    assert {item.interval for item in alerts} == {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
    assert {(item.strategy_id, item.symbol) for item in alerts} == {(kind, "BTCUSDT"), ("altcoin_" + kind, "PENGUUSDT")}
    runner.run_once()
    assert len(store.list_alerts(100)) == 14
    assert len(store.list_pending_alert_notifications()) == 2
    sid = "altcoin_" + kind
    strategy = store.get_strategy(sid)
    store.update_strategy(sid, True, {**strategy.config, "notify_intervals_by_symbol": {}}, None)
    sender = Mock()
    sender.send_strategy_message.return_value = (True, True, "test")
    NotificationWorker(store, sender).run_once()
    assert [call.args[0] for call in sender.send_strategy_message.call_args_list] == [kind]
    assert store.list_pending_alert_notifications() == []
    db.close()


def test_candles_cached_only_within_poll_and_failure_isolated(tmp_path):
    db = Database(tmp_path / "test.db", "secret")
    store = Store(db)
    configure(store, "ma")
    for sid in TECHNICAL_STRATEGY_IDS:
        old = store.get_strategy(sid)
        store.update_strategy(sid, True, {**old.config, "candle_limit": 200, "data_source": "binance_then_okx", "notify_intervals_by_symbol": {}}, None)
    market = Market(fail_symbol="BTCUSDT")
    runner = TechnicalStrategyRunner(store, market, EventBus())
    runner.run_once()
    assert len(market.calls) == 14
    assert any(call[0] == "PENGUUSDT" for call in market.calls)
    runner.run_once()
    assert len(market.calls) == 28
    db.close()


@pytest.mark.parametrize("prices", [[], [1]])
def test_insufficient_candles_do_not_generate_alerts(tmp_path, prices):
    db = Database(tmp_path / "test.db", "secret")
    store = Store(db)
    configure(store, "boll_ma_cross")
    TechnicalStrategyRunner(store, Market(prices), EventBus()).run_once()
    assert store.list_alerts() == []
    db.close()


def test_fallback_and_missing_backup():
    router = DataSourceRouter()
    router.binance = Mock(name="binance")
    router.okx = Mock(name="okx")
    router.binance.fetch_klines.side_effect = DataSourceError("offline")
    router.okx.fetch_klines.return_value = ["candle"]
    assert router.fetch_klines("PENGUUSDT", "15m", 20, "binance_then_okx")[2] == "BACKUP"
    router.okx.fetch_klines.return_value = []
    with pytest.raises(DataSourceError):
        router.fetch_klines("PENGUUSDT", "15m", 20, "binance_then_okx")
    router = DataSourceRouter()
    router.binance = Mock()
    router.binance.fetch_klines.return_value = []
    with pytest.raises(DataSourceError, match="Unsupported symbol"):
        router.fetch_klines("币安人生USDT", "15m", 20, "binance_then_okx")
    assert set(ALTCOIN_SYMBOLS) - set(OKX_SYMBOL_MAP) == {"币安人生USDT"}


@pytest.mark.parametrize("kind", BASE_TECHNICAL_STRATEGY_IDS)
def test_altcoin_notification_uses_original_template_and_precision(kind):
    row = {
        "strategy_id": "altcoin_" + kind, "symbol": "PENGUUSDT", "interval": "15m",
        "signal": "BOLL_MIDDLE_CROSS_ABOVE_MA", "close_price": 0.00001234,
        "detail_json": json.dumps({"upper": 0.000014, "middle": 0.000012, "lower": 0.00001}),
        "source": "binance_futures", "source_role": "PRIMARY", "created_at": "2026-09-28T10:00:00+00:00",
    }
    message = format_alert_notification(row)
    assert "0.00001234" in message
    assert "ALTCOIN" not in message
    assert "K线时间" not in message
    if kind == "boll":
        assert message.index("\nBOLL上轨:") < message.index("\nBOLL中轨:") < message.index("\nBOLL下轨:")


def test_alert_budget_is_independent_for_each_group(tmp_path):
    db = Database(tmp_path / "test.db", "secret")
    store = Store(db)
    configure(store, "ma")
    runner = TechnicalStrategyRunner(store, Market(), EventBus())
    runner.run_once()
    assert len(store.list_alerts(3, "main")) == 3
    assert len(store.list_alerts(3, "altcoin")) == 3
    assert all(item.strategy_id == "ma" for item in store.list_alerts(3, "main"))
    assert all(item.strategy_id == "altcoin_ma" for item in store.list_alerts(3, "altcoin"))
    db.close()


def test_notification_uses_altcoin_binding_only(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.db", "secret")
    store = Store(db)
    strategy = store.get_strategy("altcoin_ma")
    service = NotificationService(store)
    send = Mock(return_value=(True, True, "mock"))
    monkeypatch.setattr(service, "_send_to_notifier", send)
    assert service.send_strategy_message("altcoin_ma", "test")[1]
    send.assert_not_called()
    store.update_strategy("altcoin_ma", True, strategy.config, "feishu-default")
    service.send_strategy_message("altcoin_ma", "test")
    send.assert_called_once_with("feishu-default", "test", reveal=True)
    db.close()


def test_chart_api_routes_each_group_to_its_module(tmp_path, monkeypatch):
    from backend.app import main

    db = Database(tmp_path / "test.db", "secret")
    store = Store(db)
    modules = store.list_modules()
    for module in modules:
        if module.id == "charts":
            module.config["data_source"] = "okx_only"
    store.replace_modules(modules)
    market = Market()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "market_router", market)
    monkeypatch.setattr(main, "settings", replace(main.settings, run_workers=True))
    assert main.get_klines("PENGUUSDT", "15m")
    assert market.calls[-1][-1] == "binance_then_okx"
    assert main.get_klines("BTCUSDT", "15m")
    assert market.calls[-1][-1] == "okx_only"
    snapshot = main.get_snapshot()
    assert len([item for item in snapshot.symbols if item.market_group == "altcoin"]) == 8
    db.close()
