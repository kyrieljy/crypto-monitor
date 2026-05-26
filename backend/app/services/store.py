from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..api.schemas import (
    AlertEventOut,
    DashboardLayout,
    DashboardModule,
    NewsEventOut,
    NotifierTarget,
    SourceHealthOut,
    StrategyConfig,
    SymbolItem,
    WhaleDetailOut,
    WhaleTargetOut,
)
from ..core.database import Database
from ..core.security import decrypt_json, encrypt_json, mask_secret
from ..core.text import content_fingerprint, is_probably_same_statement
from ..core.time import utc_now_iso


def _json_loads(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _is_standalone_news_payload(metadata: dict[str, Any]) -> bool:
    kind = str(metadata.get("content_kind") or "").lower()
    if kind in {"image", "video", "media", "repost", "link"}:
        return True
    return bool(metadata.get("media") or metadata.get("card"))


def _chunks(items: list[int], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


MODULE_LAYOUT_DEFAULTS: dict[str, dict[str, int]] = {
    "charts": {"x": 0, "y": 0, "w": 12, "h": 16},
    "whale": {"x": 0, "y": 16, "w": 12, "h": 8},
    "trump_social": {"x": 0, "y": 24, "w": 6, "h": 7},
    "whitehouse": {"x": 6, "y": 24, "w": 6, "h": 7},
    "alerts": {"x": 0, "y": 31, "w": 6, "h": 7},
    "health": {"x": 6, "y": 31, "w": 6, "h": 7},
}

SOURCE_HEALTH_LABELS = {
    "market_data": "行情数据",
    "whitehouse_gallery": "白宫 Gallery",
    "truthbrush_api": "Truthbrush",
    "trumps_truth_rss": "TrumpTruth RSS",
}


def _default_layout_item(module_id: str, fallback_y: int) -> dict[str, int | str]:
    layout = MODULE_LAYOUT_DEFAULTS.get(module_id, {"x": 0, "y": fallback_y, "w": 6, "h": 6})
    return {"i": module_id, **layout}


def _normalize_layout(layout: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    normalized = [dict(item) for item in layout]
    chart = next((item for item in normalized if item.get("i") == "charts"), None)
    if chart is None:
        return normalized, False
    old_h = int(chart.get("h", 1))
    min_h = MODULE_LAYOUT_DEFAULTS["charts"]["h"]
    if old_h >= min_h:
        return normalized, False
    chart_y = int(chart.get("y", 0))
    old_bottom = chart_y + old_h
    delta = min_h - old_h
    chart["h"] = min_h
    for item in normalized:
        if item is chart:
            continue
        if int(item.get("y", 0)) >= old_bottom:
            item["y"] = int(item.get("y", 0)) + delta
    return normalized, True


class Store:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_symbols(self) -> list[SymbolItem]:
        rows = self.db.query("SELECT * FROM symbols ORDER BY sort_order ASC, symbol ASC")
        return [SymbolItem(symbol=row["symbol"], display_name=row["display_name"], enabled=bool(row["enabled"]), sort_order=int(row["sort_order"])) for row in rows]

    def replace_symbols(self, items: list[SymbolItem]) -> list[SymbolItem]:
        self.db.execute("DELETE FROM symbols")
        self.db.executemany(
            "INSERT INTO symbols (symbol, display_name, enabled, sort_order) VALUES (?, ?, ?, ?)",
            [(item.symbol.upper(), item.display_name, int(item.enabled), item.sort_order) for item in items],
        )
        return self.list_symbols()

    def enabled_symbols(self) -> list[str]:
        return [item.symbol for item in self.list_symbols() if item.enabled]

    def list_strategies(self) -> list[StrategyConfig]:
        rows = self.db.query(
            """
            SELECT strategy_configs.*, strategy_notifier_bindings.notifier_id
            FROM strategy_configs
            LEFT JOIN strategy_notifier_bindings ON strategy_configs.id = strategy_notifier_bindings.strategy_id
            ORDER BY strategy_configs.id
            """
        )
        result: list[StrategyConfig] = []
        for row in rows:
            config = _json_loads(row["config_json"], {})
            if row["id"] == "translation":
                secret = decrypt_json(self.state_get("translation_secret") or "", self.db.secret_key)
                config["api_key"] = mask_secret(str(secret.get("api_key") or ""))
            if row["id"] == "whale":
                secret = decrypt_json(self.state_get("whale_secret") or "", self.db.secret_key)
                config["api_key"] = mask_secret(str(secret.get("api_key") or ""))
            result.append(
                StrategyConfig(
                    id=row["id"],
                    name=row["name"],
                    type=row["type"],
                    enabled=bool(row["enabled"]),
                    config=config,
                    notifier_id=row["notifier_id"],
                    updated_at=row["updated_at"],
                )
            )
        return result

    def get_strategy(self, strategy_id: str) -> StrategyConfig | None:
        rows = [strategy for strategy in self.list_strategies() if strategy.id == strategy_id]
        return rows[0] if rows else None

    def update_strategy(self, strategy_id: str, enabled: bool, config: dict[str, Any], notifier_id: str | None) -> StrategyConfig:
        existing = self.db.query_one("SELECT * FROM strategy_configs WHERE id = ?", (strategy_id,))
        if existing is None:
            raise KeyError(strategy_id)
        config = dict(config)
        if strategy_id == "translation":
            notifier_id = None
        if strategy_id in {"translation", "whale"}:
            key = f"{strategy_id}_secret"
            secret = decrypt_json(self.state_get(key) or "", self.db.secret_key)
            api_key = str(config.pop("api_key", "") or "")
            if api_key and api_key != "********" and "..." not in api_key:
                secret["api_key"] = api_key
                self.state_set(key, encrypt_json(secret, self.db.secret_key))
        now = utc_now_iso()
        self.db.execute(
            "UPDATE strategy_configs SET enabled = ?, config_json = ?, updated_at = ? WHERE id = ?",
            (int(enabled), json.dumps(config, ensure_ascii=False), now, strategy_id),
        )
        self.db.execute(
            """
            INSERT INTO strategy_notifier_bindings (strategy_id, notifier_id)
            VALUES (?, ?)
            ON CONFLICT(strategy_id) DO UPDATE SET notifier_id = excluded.notifier_id
            """,
            (strategy_id, notifier_id),
        )
        updated = self.get_strategy(strategy_id)
        if updated is None:
            raise KeyError(strategy_id)
        return updated

    def list_notifiers(self, *, reveal: bool = False) -> list[NotifierTarget]:
        rows = self.db.query("SELECT * FROM notifier_targets ORDER BY created_at ASC")
        result: list[NotifierTarget] = []
        for row in rows:
            secrets = decrypt_json(row["secret_json"], self.db.secret_key)
            if not reveal:
                secrets = {key: mask_secret(str(value)) for key, value in secrets.items()}
            result.append(
                NotifierTarget(
                    id=row["id"],
                    name=row["name"],
                    type=row["type"],
                    enabled=bool(row["enabled"]),
                    secrets=secrets,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
        return result

    def get_notifier(self, notifier_id: str, *, reveal: bool = False) -> NotifierTarget | None:
        rows = [notifier for notifier in self.list_notifiers(reveal=reveal) if notifier.id == notifier_id]
        return rows[0] if rows else None

    def upsert_notifier(self, notifier: NotifierTarget) -> NotifierTarget:
        now = utc_now_iso()
        existing = self.get_notifier(notifier.id, reveal=True)
        secrets = dict(existing.secrets) if existing else {}
        for key, value in notifier.secrets.items():
            if value and value != "********" and "..." not in value:
                secrets[key] = value
        encrypted = encrypt_json(secrets, self.db.secret_key)
        created_at = existing.created_at if existing else now
        self.db.execute(
            """
            INSERT INTO notifier_targets (id, name, type, enabled, secret_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                enabled = excluded.enabled,
                secret_json = excluded.secret_json,
                updated_at = excluded.updated_at
            """,
            (notifier.id, notifier.name, notifier.type, int(notifier.enabled), encrypted, created_at, now),
        )
        saved = self.get_notifier(notifier.id, reveal=False)
        if saved is None:
            raise KeyError(notifier.id)
        return saved

    def delete_notifiers_not_in(self, keep_ids: set[str]) -> None:
        rows = self.db.query("SELECT id FROM notifier_targets")
        remove_ids = [row["id"] for row in rows if row["id"] not in keep_ids]
        for notifier_id in remove_ids:
            self.db.execute("UPDATE strategy_notifier_bindings SET notifier_id = NULL WHERE notifier_id = ?", (notifier_id,))
            self.db.execute("DELETE FROM notifier_targets WHERE id = ?", (notifier_id,))

    def list_modules(self) -> list[DashboardModule]:
        rows = self.db.query("SELECT * FROM dashboard_modules ORDER BY id")
        return [
            DashboardModule(
                id=row["id"],
                title=row["title"],
                enabled=bool(row["enabled"]),
                visible=bool(row["visible"]),
                config=_json_loads(row["config_json"], {}),
            )
            for row in rows
        ]

    def replace_modules(self, modules: list[DashboardModule]) -> list[DashboardModule]:
        for module in modules:
            self.db.execute(
                """
                UPDATE dashboard_modules
                SET title = ?, enabled = ?, visible = ?, config_json = ?
                WHERE id = ?
                """,
                (
                    module.title,
                    int(module.enabled),
                    int(module.visible),
                    json.dumps(module.config, ensure_ascii=False),
                    module.id,
                ),
            )
        self._ensure_visible_modules_have_layout(modules)
        return self.list_modules()

    def _ensure_visible_modules_have_layout(self, modules: list[DashboardModule]) -> None:
        layout = self.get_layout()
        existing = {str(item.get("i")) for item in layout.layout if item.get("i")}
        next_y = max((int(item.get("y", 0)) + int(item.get("h", 1)) for item in layout.layout), default=0)
        changed = False
        for module in modules:
            if not module.enabled or not module.visible or module.id in existing:
                continue
            item = _default_layout_item(module.id, next_y)
            layout.layout.append(item)
            existing.add(module.id)
            next_y = max(next_y, int(item.get("y", 0)) + int(item.get("h", 1)))
            changed = True
        if changed:
            self.save_layout(layout)

    def get_layout(self) -> DashboardLayout:
        row = self.db.query_one("SELECT * FROM dashboard_layouts WHERE id = 'default'")
        if row is None:
            return DashboardLayout(id="default", theme="dark", layout=[])
        raw_layout = _json_loads(row["layout_json"], [])
        layout, changed = _normalize_layout(raw_layout)
        if changed:
            self.db.execute(
                "UPDATE dashboard_layouts SET layout_json = ?, updated_at = ? WHERE id = 'default'",
                (json.dumps(layout, ensure_ascii=False), utc_now_iso()),
            )
        return DashboardLayout(
            id=row["id"],
            theme=row["theme"],
            layout=layout,
            updated_at=row["updated_at"],
        )

    def save_layout(self, layout: DashboardLayout) -> DashboardLayout:
        now = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO dashboard_layouts (id, theme, layout_json, updated_at)
            VALUES ('default', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                theme = excluded.theme,
                layout_json = excluded.layout_json,
                updated_at = excluded.updated_at
            """,
            (layout.theme, json.dumps(layout.layout, ensure_ascii=False), now),
        )
        return self.get_layout()

    def create_alert(
        self,
        *,
        strategy_id: str,
        symbol: str,
        interval: str,
        signal: str,
        severity: str,
        message: str,
        detail: dict[str, Any],
        candle_open_time_ms: int | None,
        close_price: float | None,
        source: str,
        source_role: str,
        dedupe_key: str,
        suppress_notification: bool = False,
    ) -> int | None:
        now = utc_now_iso()
        try:
            cursor = self.db.execute(
                """
                INSERT INTO alert_events (
                    strategy_id, symbol, interval, signal, severity, message, detail_json,
                    candle_open_time_ms, close_price, source, source_role, dedupe_key,
                    notification_sent, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    symbol,
                    interval,
                    signal,
                    severity,
                    message,
                    json.dumps(detail, ensure_ascii=False),
                    candle_open_time_ms,
                    close_price,
                    source,
                    source_role,
                    dedupe_key,
                    int(suppress_notification),
                    now,
                ),
            )
        except Exception:
            return None
        return int(cursor.lastrowid)

    def list_alerts(self, limit: int = 80) -> list[AlertEventOut]:
        rows = self.db.query("SELECT * FROM alert_events ORDER BY id DESC LIMIT ?", (limit,))
        return [
            AlertEventOut(
                id=int(row["id"]),
                strategy_id=row["strategy_id"],
                symbol=row["symbol"],
                interval=row["interval"],
                signal=row["signal"],
                severity=row["severity"],
                message=row["message"],
                detail=_json_loads(row["detail_json"], {}),
                candle_open_time_ms=row["candle_open_time_ms"],
                close_price=row["close_price"],
                source=row["source"],
                source_role=row["source_role"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def upsert_news_event(
        self,
        *,
        source_type: str,
        source_name: str,
        event_id: str,
        published_at_utc: str,
        title: str,
        translated_title: str,
        speaker: str,
        content: str,
        translated_summary: str,
        url: str,
        raw_hash: str,
        metadata: dict[str, Any],
        suppress_notification: bool,
    ) -> tuple[str, int]:
        now = utc_now_iso()
        content_hash = content_fingerprint(title, content)
        standalone_payload = _is_standalone_news_payload(metadata)
        existing = self.db.query_one(
            """
            SELECT * FROM news_events
            WHERE (source_name = ? AND event_id = ?)
               OR (source_name = ? AND url = ?)
            LIMIT 1
            """,
            (source_name, event_id, source_name, url),
        )
        if existing:
            self.db.execute(
                """
                UPDATE news_events
                SET published_at_utc = ?, title = ?, translated_title = ?, speaker = ?,
                    content = ?, translated_summary = ?, raw_hash = ?, content_hash = ?,
                    metadata_json = ?, last_seen_utc = ?,
                    merged_into_event_pk = CASE WHEN ? THEN NULL ELSE merged_into_event_pk END
                WHERE id = ?
                """,
                (
                    published_at_utc,
                    title,
                    translated_title,
                    speaker,
                    content,
                    translated_summary,
                    raw_hash,
                    content_hash,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    now,
                    int(standalone_payload),
                    int(existing["id"]),
                ),
            )
            return "updated_existing_source_record", int(existing["id"])

        merge_candidate = None if standalone_payload else self._find_news_merge_candidate(title, content, content_hash)
        merged_into = int(merge_candidate["id"]) if merge_candidate is not None else None
        notification_sent = 1 if suppress_notification or merged_into is not None else 0
        cursor = self.db.execute(
            """
            INSERT INTO news_events (
                source_type, source_name, event_id, published_at_utc, title, translated_title,
                speaker, content, translated_summary, url, raw_hash, content_hash, metadata_json,
                first_seen_utc, last_seen_utc, merged_into_event_pk, notification_sent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type,
                source_name,
                event_id,
                published_at_utc,
                title,
                translated_title,
                speaker,
                content,
                translated_summary,
                url,
                raw_hash,
                content_hash,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                now,
                now,
                merged_into,
                notification_sent,
            ),
        )
        return ("merged_duplicate" if merged_into is not None else "inserted_new"), int(cursor.lastrowid)

    def _find_news_merge_candidate(self, title: str, content: str, content_hash: str):
        rows = self.db.query(
            """
            SELECT * FROM news_events
            WHERE merged_into_event_pk IS NULL
            ORDER BY published_at_utc DESC
            LIMIT 50
            """
        )
        for row in rows:
            if row["content_hash"] == content_hash:
                return row
            if is_probably_same_statement(row["title"], row["content"], title, content):
                return row
        return None

    def list_news(self, limit: int = 80) -> list[NewsEventOut]:
        rows = self.db.query(
            """
            SELECT * FROM news_events
            WHERE merged_into_event_pk IS NULL
            ORDER BY published_at_utc DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            NewsEventOut(
                id=int(row["id"]),
                source_type=row["source_type"],
                source_name=row["source_name"],
                published_at_utc=row["published_at_utc"],
                title=row["title"],
                translated_title=row["translated_title"],
                speaker=row["speaker"],
                content=row["content"],
                translated_summary=row["translated_summary"],
                url=row["url"],
                metadata=_json_loads(row["metadata_json"], {}),
                first_seen_utc=row["first_seen_utc"],
                notification_sent=bool(row["notification_sent"]),
            )
            for row in rows
        ]

    def list_news_rows_by_ids(self, ids: list[int]):
        unique_ids = list(dict.fromkeys(int(item) for item in ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        return self.db.query(
            f"""
            SELECT * FROM news_events
            WHERE id IN ({placeholders})
              AND merged_into_event_pk IS NULL
            ORDER BY published_at_utc DESC, id DESC
            """,
            tuple(unique_ids),
        )

    def update_news_translation(self, event_id: int, translated_title: str, translated_summary: str) -> None:
        self.db.execute(
            """
            UPDATE news_events
            SET translated_title = ?, translated_summary = ?, last_seen_utc = ?
            WHERE id = ?
            """,
            (translated_title, translated_summary, utc_now_iso(), event_id),
        )

    def cleanup_old_data(
        self,
        *,
        alert_retention_days: int,
        news_retention_days: int,
        whale_retention_days: int,
        delete_pending_notifications: bool,
        vacuum_after_cleanup: bool,
        now: datetime | None = None,
    ) -> dict[str, int | bool | str]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        alert_cutoff = (current - timedelta(days=max(1, int(alert_retention_days)))).isoformat()
        news_cutoff = (current - timedelta(days=max(1, int(news_retention_days)))).isoformat()
        whale_cutoff = (current - timedelta(days=max(1, int(whale_retention_days)))).isoformat()
        pending_filter = "" if delete_pending_notifications else " AND notification_sent = 1"

        alert_deleted = self.db.execute(
            f"DELETE FROM alert_events WHERE created_at < ?{pending_filter}",
            (alert_cutoff,),
        ).rowcount

        news_rows = self.db.query(
            f"SELECT id FROM news_events WHERE published_at_utc < ?{pending_filter}",
            (news_cutoff,),
        )
        news_deleted = 0
        news_ids = [int(row["id"]) for row in news_rows]
        for chunk in _chunks(news_ids, 500):
            placeholders = ",".join("?" for _ in chunk)
            news_deleted += self.db.execute(
                f"""
                DELETE FROM news_events
                WHERE (id IN ({placeholders}) OR merged_into_event_pk IN ({placeholders}))
                {"" if delete_pending_notifications else "AND notification_sent = 1"}
                """,
                (*chunk, *chunk),
            ).rowcount

        whale_deleted = self.db.execute(
            "DELETE FROM whale_events WHERE occurred_at_utc < ?",
            (whale_cutoff,),
        ).rowcount

        total_deleted = int(alert_deleted) + int(news_deleted) + int(whale_deleted)
        vacuumed = False
        if total_deleted > 0 and vacuum_after_cleanup:
            self.db.execute("VACUUM")
            vacuumed = True

        result: dict[str, int | bool | str] = {
            "alert_deleted": int(alert_deleted),
            "news_deleted": int(news_deleted),
            "whale_deleted": int(whale_deleted),
            "total_deleted": total_deleted,
            "vacuumed": vacuumed,
            "ran_at_utc": current.isoformat(),
        }
        self.state_set("cleanup_last_result", json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result

    def list_pending_alert_notifications(self):
        return self.db.query("SELECT * FROM alert_events WHERE notification_sent = 0 ORDER BY id ASC LIMIT 30")

    def list_pending_news_notifications(self):
        return self.db.query(
            """
            SELECT * FROM news_events
            WHERE notification_sent = 0 AND merged_into_event_pk IS NULL
            ORDER BY published_at_utc ASC
            LIMIT 30
            """
        )

    def mark_alert_notification(self, event_id: int, *, ok: bool, error: str | None = None) -> None:
        self.db.execute(
            """
            UPDATE alert_events
            SET notification_sent = CASE WHEN ? THEN 1 ELSE notification_sent END,
                notification_attempts = notification_attempts + 1,
                last_notification_error = ?
            WHERE id = ?
            """,
            (int(ok), None if ok else (error or "")[:1000], event_id),
        )

    def mark_news_notification(self, event_id: int, *, ok: bool, error: str | None = None) -> None:
        self.db.execute(
            """
            UPDATE news_events
            SET notification_sent = CASE WHEN ? THEN 1 ELSE notification_sent END,
                notification_attempts = notification_attempts + 1,
                last_notification_error = ?
            WHERE id = ?
            """,
            (int(ok), None if ok else (error or "")[:1000], event_id),
        )

    def record_source_success(self, source_name: str, label: str) -> None:
        self.db.execute(
            """
            INSERT INTO source_health (source_name, label, status, last_success_utc, last_error_utc, last_error_message)
            VALUES (?, ?, 'ok', ?, NULL, NULL)
            ON CONFLICT(source_name) DO UPDATE SET
                label = excluded.label,
                status = 'ok',
                last_success_utc = excluded.last_success_utc,
                last_error_utc = NULL,
                last_error_message = NULL
            """,
            (source_name, label, utc_now_iso()),
        )

    def record_source_error(self, source_name: str, label: str, error: str) -> None:
        self.db.execute(
            """
            INSERT INTO source_health (source_name, label, status, last_success_utc, last_error_utc, last_error_message)
            VALUES (?, ?, 'error', NULL, ?, ?)
            ON CONFLICT(source_name) DO UPDATE SET
                label = excluded.label,
                status = 'error',
                last_error_utc = excluded.last_error_utc,
                last_error_message = excluded.last_error_message
            """,
            (source_name, label, utc_now_iso(), error[:1000]),
        )

    def list_health(self) -> list[SourceHealthOut]:
        rows = self.db.query("SELECT * FROM source_health ORDER BY source_name ASC")
        return [
                SourceHealthOut(
                    source_name=row["source_name"],
                    label=SOURCE_HEALTH_LABELS.get(row["source_name"], row["label"]),
                    status=row["status"],
                    last_success_utc=row["last_success_utc"],
                    last_error_utc=row["last_error_utc"],
                last_error_message=row["last_error_message"],
            )
            for row in rows
        ]

    def state_get(self, key: str) -> str | None:
        row = self.db.query_one("SELECT state_value FROM app_state WHERE state_key = ?", (key,))
        return str(row["state_value"]) if row else None

    def state_set(self, key: str, value: str) -> None:
        self.db.execute(
            """
            INSERT INTO app_state (state_key, state_value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET state_value = excluded.state_value
            """,
            (key, value),
        )

    def list_whale_targets(self) -> list[WhaleTargetOut]:
        rows = self.db.query("SELECT * FROM whale_targets ORDER BY updated_at DESC, label ASC")
        return [
            WhaleTargetOut(
                id=row["id"],
                label=row["label"],
                address_or_subject=row["address_or_subject"],
                enabled=bool(row["enabled"]),
                config=_json_loads(row["config_json"], {}),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_whale_detail(self, target_id: str) -> WhaleDetailOut | None:
        target = next((item for item in self.list_whale_targets() if item.id == target_id), None)
        if target is None:
            return None
        events = self.db.query(
            """
            SELECT * FROM whale_events
            WHERE target_id = ?
            ORDER BY occurred_at_utc DESC
            LIMIT 40
            """,
            (target_id,),
        )
        recent_events = [
            {
                "id": int(row["id"]),
                "provider": row["provider"],
                "action_type": row["action_type"],
                "summary": row["summary"],
                "payload": _json_loads(row["payload_json"], {}),
                "occurred_at_utc": row["occurred_at_utc"],
            }
            for row in events
        ]
        config = target.config
        return WhaleDetailOut(
            target=target,
            recent_events=recent_events,
            positions=list(config.get("positions", [])),
            holdings=list(config.get("holdings", [])),
        )
