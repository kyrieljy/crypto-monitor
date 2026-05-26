from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .security import encrypt_json
from .time import utc_now_iso


DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ZECUSDT"]
DEFAULT_WHITEHOUSE_INCLUDE_KEYWORDS = [
    "Trump",
    "remarks",
    "speaks",
    "press",
    "announcement",
    "tariff",
    "economy",
    "crypto",
    "bitcoin",
    "China",
    "Iran",
    "Fed",
    "Treasury",
    "energy",
    "oil",
]


class Database:
    def __init__(self, path: Path, secret_key: str) -> None:
        self.path = path
        self.secret_key = secret_key
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
        self.seed_defaults()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cursor

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS symbols (
                    symbol TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS strategy_configs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notifier_targets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    secret_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_notifier_bindings (
                    strategy_id TEXT PRIMARY KEY,
                    notifier_id TEXT,
                    FOREIGN KEY(strategy_id) REFERENCES strategy_configs(id),
                    FOREIGN KEY(notifier_id) REFERENCES notifier_targets(id)
                );

                CREATE TABLE IF NOT EXISTS dashboard_modules (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    visible INTEGER NOT NULL DEFAULT 1,
                    config_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dashboard_layouts (
                    id TEXT PRIMARY KEY,
                    theme TEXT NOT NULL DEFAULT 'dark',
                    layout_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    candle_open_time_ms INTEGER,
                    close_price REAL,
                    source TEXT NOT NULL,
                    source_role TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    notification_sent INTEGER NOT NULL DEFAULT 0,
                    notification_attempts INTEGER NOT NULL DEFAULT 0,
                    last_notification_error TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS news_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    published_at_utc TEXT NOT NULL,
                    title TEXT NOT NULL,
                    translated_title TEXT NOT NULL DEFAULT '',
                    speaker TEXT NOT NULL,
                    content TEXT NOT NULL,
                    translated_summary TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    raw_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    first_seen_utc TEXT NOT NULL,
                    last_seen_utc TEXT NOT NULL,
                    merged_into_event_pk INTEGER,
                    notification_sent INTEGER NOT NULL DEFAULT 0,
                    notification_attempts INTEGER NOT NULL DEFAULT 0,
                    last_notification_error TEXT,
                    UNIQUE(source_name, event_id),
                    UNIQUE(source_name, url)
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    source_name TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_success_utc TEXT,
                    last_error_utc TEXT,
                    last_error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS app_state (
                    state_key TEXT PRIMARY KEY,
                    state_value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS whale_targets (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    address_or_subject TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS whale_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    event_key TEXT,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    notification_required INTEGER NOT NULL DEFAULT 0,
                    notification_sent INTEGER NOT NULL DEFAULT 1,
                    notification_attempts INTEGER NOT NULL DEFAULT 0,
                    last_notification_error TEXT
                );

                CREATE TABLE IF NOT EXISTS whale_snapshots (
                    target_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate_schema_columns()
            self.conn.commit()

    def _migrate_schema_columns(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(whale_events)").fetchall()}
        additions = {
            "event_key": "ALTER TABLE whale_events ADD COLUMN event_key TEXT",
            "notification_required": "ALTER TABLE whale_events ADD COLUMN notification_required INTEGER NOT NULL DEFAULT 0",
            "notification_sent": "ALTER TABLE whale_events ADD COLUMN notification_sent INTEGER NOT NULL DEFAULT 1",
            "notification_attempts": "ALTER TABLE whale_events ADD COLUMN notification_attempts INTEGER NOT NULL DEFAULT 0",
            "last_notification_error": "ALTER TABLE whale_events ADD COLUMN last_notification_error TEXT",
        }
        for column, sql in additions.items():
            if column not in columns:
                self.conn.execute(sql)
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_whale_events_event_key
            ON whale_events(event_key)
            WHERE event_key IS NOT NULL
            """
        )

    def seed_defaults(self) -> None:
        now = utc_now_iso()
        rows = [(symbol, symbol.replace("USDT", "/USDT"), 1, index) for index, symbol in enumerate(DEFAULT_SYMBOLS)]
        self.executemany(
            """
            INSERT OR IGNORE INTO symbols (symbol, display_name, enabled, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )

        strategies = [
            (
                "kdj",
                "KDJ 策略",
                "technical",
                1,
                {
                    "symbols": DEFAULT_SYMBOLS,
                    "intervals": ["5m", "15m", "1h"],
                    "period": 26,
                    "k_smoothing": 20,
                    "d_smoothing": 9,
                    "alert_on_live_candle": False,
                    "candle_limit": 200,
                    "poll_seconds": 10,
                    "data_source": "okx_only",
                },
            ),
            (
                "ma",
                "MA 均线策略",
                "technical",
                1,
                {
                    "symbols": DEFAULT_SYMBOLS,
                    "interval": "1d",
                    "fast_period": 25,
                    "slow_period": 99,
                    "alert_on_live_candle": True,
                    "candle_limit": 200,
                    "poll_seconds": 10,
                    "data_source": "okx_only",
                },
            ),
            (
                "boll",
                "BOLL 布林带策略",
                "technical",
                1,
                {
                    "symbols": DEFAULT_SYMBOLS,
                    "intervals": ["1h", "4h"],
                    "period": 20,
                    "stddev": 2.0,
                    "alert_on_live_candle": False,
                    "candle_limit": 200,
                    "poll_seconds": 10,
                    "data_source": "okx_only",
                },
            ),
            (
                "trump_social",
                "特朗普社媒",
                "news",
                1,
                {
                    "enable_truthbrush": False,
                    "enable_truth_social": True,
                    "truthsocial_handle": "realDonaldTrump",
                    "truth_social_feed_url": "https://www.trumpstruth.org/feed",
                    "poll_seconds": 1200,
                    "immediate_categories": [
                        "market_sentiment",
                        "foreign_policy",
                        "military",
                        "election_integrity",
                        "immigration",
                        "federal_workers",
                        "personal_or_rhetorical",
                        "no_content",
                    ],
                },
            ),
            (
                "whitehouse",
                "白宫发言新闻",
                "news",
                1,
                {
                    "enabled": True,
                    "whitehouse_gallery_url": "https://www.whitehouse.gov/gallery/",
                    "include_keywords": DEFAULT_WHITEHOUSE_INCLUDE_KEYWORDS,
                    "exclude_keywords": [],
                    "poll_seconds": 1200,
                },
            ),
            (
                "translation",
                "大模型翻译",
                "system",
                0,
                {
                    "enabled": False,
                    "api_url": "https://open.bigmodel.cn/api/paas/v4",
                    "model": "",
                    "temperature": 0.2,
                },
            ),
            (
                "cleanup",
                "服务器清理策略",
                "system",
                1,
                {
                    "enabled": True,
                    "schedule_time": "12:30",
                    "timezone": "Asia/Shanghai",
                    "alert_retention_days": 30,
                    "news_retention_days": 60,
                    "whale_retention_days": 90,
                    "delete_pending_notifications": True,
                    "vacuum_after_cleanup": True,
                },
            ),
            (
                "whale",
                "巨鲸监控",
                "whale",
                0,
                {
                    "enabled": False,
                    "provider": "hyperliquid_debank",
                    "hyperliquid_enabled": True,
                    "hyperliquid_base_url": "https://api.hyperliquid.xyz",
                    "debank_enabled": False,
                    "debank_base_url": "https://pro-openapi.debank.com",
                    "etherscan_enabled": False,
                    "poll_seconds": 300,
                    "trade_monitor_enabled": True,
                    "trade_notification_enabled": True,
                    "trade_poll_seconds": 120,
                    "extended_poll_seconds": 1800,
                    "notify_large_trades": True,
                    "trade_min_notional_usd": 100000,
                    "trade_coin_thresholds": {"ETH": 100, "BTC": 5, "SOL": 10000},
                    "initial_fill_sync_mode": "cursor_only",
                    "position_change_alert_pct": 25,
                    "min_position_value_usd": 10000,
                    "liquidation_distance_pct": 5,
                    "targets": [{"label": "麻吉", "subject": "", "enabled": False}],
                },
            ),
        ]
        for strategy_id, name, kind, enabled, config in strategies:
            self.execute(
                """
                INSERT OR IGNORE INTO strategy_configs (id, name, type, enabled, config_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (strategy_id, name, kind, enabled, json.dumps(config, ensure_ascii=False), now),
            )
            self.execute("UPDATE strategy_configs SET name = ?, type = ? WHERE id = ?", (name, kind, strategy_id))
        self._migrate_strategy_config_defaults(now)

        modules = [
            ("charts", "行情与策略监控", 1, 1, {"minW": 12, "minH": 16, "data_source": "okx_then_binance"}),
            ("trump_social", "特朗普社媒监控", 1, 1, {}),
            ("whitehouse", "白宫发言新闻", 1, 1, {}),
            ("whale", "巨鲸与聪明钱动态", 1, 1, {}),
            ("alerts", "最近告警", 1, 1, {}),
            ("health", "数据源健康", 1, 1, {}),
        ]
        for module_id, title, enabled, visible, config in modules:
            self.execute(
                """
                INSERT OR IGNORE INTO dashboard_modules (id, title, enabled, visible, config_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (module_id, title, enabled, visible, json.dumps(config, ensure_ascii=False)),
            )
            self.execute("UPDATE dashboard_modules SET title = ? WHERE id = ?", (title, module_id))
        self._migrate_dashboard_module_defaults()

        layout = [
            {"i": "charts", "x": 0, "y": 0, "w": 12, "h": 16},
            {"i": "whale", "x": 0, "y": 16, "w": 12, "h": 8},
            {"i": "trump_social", "x": 0, "y": 24, "w": 6, "h": 7},
            {"i": "whitehouse", "x": 6, "y": 24, "w": 6, "h": 7},
            {"i": "alerts", "x": 0, "y": 31, "w": 6, "h": 7},
            {"i": "health", "x": 6, "y": 31, "w": 6, "h": 7},
        ]
        self.execute(
            """
            INSERT OR IGNORE INTO dashboard_layouts (id, theme, layout_json, updated_at)
            VALUES ('default', 'dark', ?, ?)
            """,
            (json.dumps(layout, ensure_ascii=False), now),
        )
        if self.query_one("SELECT state_value FROM app_state WHERE state_key = 'ui_layout_version'") is None:
            for module_id, _, _, visible, _ in modules:
                self.execute("UPDATE dashboard_modules SET visible = ? WHERE id = ?", (visible, module_id))
            self.execute(
                "UPDATE dashboard_layouts SET layout_json = ?, updated_at = ? WHERE id = 'default'",
                (json.dumps(layout, ensure_ascii=False), now),
            )
            self.execute(
                "INSERT INTO app_state (state_key, state_value) VALUES ('ui_layout_version', '2')",
            )
        layout_version = self.query_one("SELECT state_value FROM app_state WHERE state_key = 'ui_layout_version'")
        if layout_version is None or str(layout_version["state_value"]) != "3":
            for module_id in {"charts", "whale", "trump_social", "whitehouse", "alerts", "health"}:
                self.execute("UPDATE dashboard_modules SET visible = 1 WHERE id = ?", (module_id,))
            self.execute(
                "UPDATE dashboard_layouts SET layout_json = ?, updated_at = ? WHERE id = 'default'",
                (json.dumps(layout, ensure_ascii=False), now),
            )
            self.execute(
                """
                INSERT INTO app_state (state_key, state_value)
                VALUES ('ui_layout_version', '3')
                ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
                """,
            )
        self._remove_obsolete_dashboard_modules({"kdj", "ma", "boll"})

        default_secret = encrypt_json({"webhook_url": "", "bot_token": "", "chat_id": ""}, self.secret_key)
        self.execute(
            """
            INSERT OR IGNORE INTO notifier_targets (id, name, type, enabled, secret_json, created_at, updated_at)
            VALUES ('feishu-default', '默认飞书机器人', 'feishu', 0, ?, ?, ?)
            """,
            (default_secret, now, now),
        )
        for strategy_id in ("kdj", "ma", "boll", "trump_social", "whitehouse", "whale"):
            self.execute(
                """
                INSERT OR IGNORE INTO strategy_notifier_bindings (strategy_id, notifier_id)
                VALUES (?, 'feishu-default')
                """,
                (strategy_id,),
            )
        self.execute(
            """
            INSERT OR IGNORE INTO whale_targets (id, label, address_or_subject, enabled, config_json, updated_at)
            VALUES ('machi', '麻吉大哥', '0x020ca66c30bec2c4fe3861a94e4db4a498a35872', 1, ?, ?)
            """,
            (
                json.dumps(
                    {
                        "tags": ["聪明钱", "重点关注"],
                        "addresses": ["0x020ca66c30bec2c4fe3861a94e4db4a498a35872"],
                        "source_url": "https://hyperdash.info/trader/0x020ca66c30bec2c4fe3861a94e4db4a498a35872",
                        "current_operation_amount": None,
                        "positions": [],
                        "holdings": [],
                        "defi_positions": [],
                        "open_orders": [],
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )

    def _migrate_strategy_config_defaults(self, now: str) -> None:
        social_row = self.query_one("SELECT config_json FROM strategy_configs WHERE id = 'trump_social'")
        migration_row = self.query_one("SELECT state_value FROM app_state WHERE state_key = 'truth_social_source_mode_v1'")
        if social_row is not None and migration_row is None:
            config = json.loads(social_row["config_json"])
            if config.get("enable_truthbrush") is True and config.get("enable_truth_social") is True:
                config["enable_truthbrush"] = False
                config["enable_truth_social"] = True
                self.execute(
                    "UPDATE strategy_configs SET config_json = ?, updated_at = ? WHERE id = 'trump_social'",
                    (json.dumps(config, ensure_ascii=False), now),
                )
            self.execute(
                "INSERT OR IGNORE INTO app_state (state_key, state_value) VALUES ('truth_social_source_mode_v1', 'rss_only')",
            )

        whitehouse_row = self.query_one("SELECT config_json FROM strategy_configs WHERE id = 'whitehouse'")
        if whitehouse_row is not None:
            config = json.loads(whitehouse_row["config_json"])
            changed = False
            if "include_keywords" not in config:
                config["include_keywords"] = DEFAULT_WHITEHOUSE_INCLUDE_KEYWORDS
                changed = True
            if "exclude_keywords" not in config:
                config["exclude_keywords"] = []
                changed = True
            if changed:
                self.execute(
                    "UPDATE strategy_configs SET config_json = ?, updated_at = ? WHERE id = 'whitehouse'",
                    (json.dumps(config, ensure_ascii=False), now),
                )

        whale_row = self.query_one("SELECT config_json FROM strategy_configs WHERE id = 'whale'")
        if whale_row is not None:
            config = json.loads(whale_row["config_json"])
            defaults = {
                "provider": "hyperliquid_debank",
                "hyperliquid_enabled": True,
                "hyperliquid_base_url": "https://api.hyperliquid.xyz",
                "debank_enabled": False,
                "debank_base_url": "https://pro-openapi.debank.com",
                "etherscan_enabled": False,
                "poll_seconds": 300,
                "trade_monitor_enabled": True,
                "trade_notification_enabled": True,
                "trade_poll_seconds": 120,
                "extended_poll_seconds": 1800,
                "notify_large_trades": True,
                "trade_min_notional_usd": 100000,
                "trade_coin_thresholds": {"ETH": 100, "BTC": 5, "SOL": 10000},
                "initial_fill_sync_mode": "cursor_only",
                "position_change_alert_pct": 25,
                "min_position_value_usd": 10000,
                "liquidation_distance_pct": 5,
            }
            changed = False
            for key, value in defaults.items():
                if key not in config:
                    config[key] = value
                    changed = True
            for key, minimum in {"poll_seconds": 300, "trade_poll_seconds": 120, "extended_poll_seconds": 1800}.items():
                try:
                    current = int(config.get(key, 0))
                except (TypeError, ValueError):
                    current = 0
                if current < minimum:
                    config[key] = minimum
                    changed = True
            if changed:
                self.execute(
                    "UPDATE strategy_configs SET config_json = ?, updated_at = ? WHERE id = 'whale'",
                    (json.dumps(config, ensure_ascii=False), now),
                )

        machi_address = "0x020ca66c30bec2c4fe3861a94e4db4a498a35872"
        machi_source_url = f"https://hyperdash.info/trader/{machi_address}"
        machi_row = self.query_one("SELECT address_or_subject, config_json FROM whale_targets WHERE id = 'machi'")
        if machi_row is not None and str(machi_row["address_or_subject"]).strip().lower() == "0x020c...5872":
            config = json.loads(machi_row["config_json"])
            config["addresses"] = [machi_address]
            if not config.get("source_url"):
                config["source_url"] = machi_source_url
            self.execute(
                "UPDATE whale_targets SET address_or_subject = ?, config_json = ?, updated_at = ? WHERE id = 'machi'",
                (machi_address, json.dumps(config, ensure_ascii=False), now),
            )

    def _migrate_dashboard_module_defaults(self) -> None:
        charts_row = self.query_one("SELECT config_json FROM dashboard_modules WHERE id = 'charts'")
        if charts_row is None:
            return
        config = json.loads(charts_row["config_json"])
        if "data_source" in config:
            return
        config["data_source"] = "okx_then_binance"
        self.execute(
            "UPDATE dashboard_modules SET config_json = ? WHERE id = 'charts'",
            (json.dumps(config, ensure_ascii=False),),
        )

    def _remove_obsolete_dashboard_modules(self, module_ids: set[str]) -> None:
        for module_id in module_ids:
            self.execute("DELETE FROM dashboard_modules WHERE id = ?", (module_id,))

        rows = self.query("SELECT id, layout_json FROM dashboard_layouts")
        for row in rows:
            try:
                layout = json.loads(row["layout_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(layout, list):
                continue
            filtered = [
                item
                for item in layout
                if not isinstance(item, dict) or str(item.get("i")) not in module_ids
            ]
            if len(filtered) != len(layout):
                self.execute(
                    "UPDATE dashboard_layouts SET layout_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(filtered, ensure_ascii=False), utc_now_iso(), row["id"]),
                )
