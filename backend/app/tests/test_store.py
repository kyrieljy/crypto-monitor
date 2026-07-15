from __future__ import annotations

from pathlib import Path

from backend.app.api.schemas import DashboardModule, NotifierTarget
from backend.app.core.database import Database
from backend.app.services.store import Store


def test_notifier_secrets_are_masked(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    store.upsert_notifier(
        NotifierTarget(
            id="robot",
            name="飞书机器人",
            type="feishu",
            enabled=True,
            secrets={"webhook_url": "https://example.com/hook/abcdef123456"},
            created_at="",
            updated_at="",
        )
    )

    masked = store.get_notifier("robot")
    revealed = store.get_notifier("robot", reveal=True)

    assert masked is not None
    assert revealed is not None
    assert masked.secrets["webhook_url"].startswith("http")
    assert "abcdef123456" not in masked.secrets["webhook_url"]
    assert revealed.secrets["webhook_url"].endswith("abcdef123456")


def test_strategy_update_hot_config(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    strategy = store.get_strategy("boll")
    assert strategy is not None
    updated = store.update_strategy(
        "boll",
        True,
        {**strategy.config, "period": 30, "stddev": 2.5},
        strategy.notifier_id,
    )
    assert updated.config["period"] == 30
    assert updated.config["stddev"] == 2.5


def test_technical_strategy_notification_symbol_defaults_and_empty_selection(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    store = Store(Database(database_path, "secret"))
    for strategy_id in ("kdj", "ma", "boll"):
        strategy = store.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.config["notify_symbols"] == strategy.config["symbols"]

    strategy = store.get_strategy("boll_ma_cross")
    assert strategy is not None
    assert strategy.enabled is True
    assert strategy.notifier_id is None
    assert strategy.config["boll_period"] == 20
    assert strategy.config["ma_period"] == 99
    assert strategy.config["notify_symbols"] == strategy.config["symbols"]

    updated = store.update_strategy(
        "boll_ma_cross",
        True,
        {**strategy.config, "notify_symbols": []},
        None,
    )
    assert updated.config["notify_symbols"] == []
    assert updated.notifier_id is None

    store.db.close()
    reopened = Store(Database(database_path, "secret"))
    persisted = reopened.get_strategy("boll_ma_cross")
    assert persisted is not None
    assert persisted.config["notify_symbols"] == []


def test_legacy_technical_strategy_migrates_notification_symbols(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    store = Store(Database(database_path, "secret"))
    strategy = store.get_strategy("kdj")
    assert strategy is not None
    legacy_config = {key: value for key, value in strategy.config.items() if key != "notify_symbols"}
    store.update_strategy("kdj", True, legacy_config, strategy.notifier_id)
    store.db.close()

    migrated_store = Store(Database(database_path, "secret"))
    migrated = migrated_store.get_strategy("kdj")
    assert migrated is not None
    assert migrated.config["notify_symbols"] == migrated.config["symbols"]


def test_default_alerts_module_stays_in_layout(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    assert any(item["i"] == "alerts" for item in store.get_layout().layout)

    modules = [
        DashboardModule(
            id=module.id,
            title=module.title,
            enabled=module.enabled,
            visible=True if module.id == "alerts" else module.visible,
            config=module.config,
        )
        for module in store.list_modules()
    ]
    store.replace_modules(modules)

    assert any(item["i"] == "alerts" for item in store.get_layout().layout)


def test_suppressed_alert_is_not_pending_notification(tmp_path: Path) -> None:
    store = Store(Database(tmp_path / "test.db", "secret"))
    inserted_id = store.create_alert(
        strategy_id="kdj",
        symbol="BTCUSDT",
        interval="4h",
        signal="J_CROSS_ABOVE_K",
        severity="warning",
        message="dashboard only",
        detail={},
        candle_open_time_ms=1,
        close_price=1.0,
        source="okx_swap",
        source_role="PRIMARY",
        dedupe_key="dashboard-only",
        suppress_notification=True,
    )

    assert inserted_id is not None
    assert store.list_pending_alert_notifications() == []
