from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..api.schemas import (
    AlertEventOut,
    BtcLargeTransferListOut,
    BtcLargeTransferOut,
    BtcLargeTransferStatsOut,
    DashboardLayout,
    DashboardModule,
    NewsEventOut,
    NotifierTarget,
    SourceHealthOut,
    StrategyConfig,
    SymbolItem,
    WhaleAddressCandidate,
    WhaleAddressResolveResponse,
    WhaleDetailOut,
    WhaleTargetOut,
    WhaleTargetUpsert,
)
from ..core.database import Database
from ..core.security import decrypt_json, encrypt_json, mask_secret
from ..core.text import content_fingerprint, is_probably_same_statement
from ..core.time import utc_now_iso
from .whale import BlackRockFreeProvider as BlackRockFreeAddressBuilder, extract_addresses, resolve_address_candidates


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


def _should_store_secret(value: str) -> bool:
    return bool(value and value != "********" and "..." not in value)


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", value.strip().lower()).strip("-")
    return text[:64] or "whale-target"


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _timestamp_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _address_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _operation_key(operation: dict[str, Any]) -> str:
    return ":".join(
        [
            str(operation.get("txid") or ""),
            str(operation.get("chain") or ""),
            str(operation.get("asset") or ""),
            str(operation.get("direction") or operation.get("behavior") or ""),
            str(operation.get("amount") or operation.get("amount_btc") or operation.get("amount_eth") or ""),
        ]
    )


def _merge_operation_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            key = _operation_key(item)
            if key.strip(":"):
                rows[key] = item
    return sorted(rows.values(), key=lambda item: _safe_int(item.get("timestamp_ms")), reverse=True)


def _news_signal_key(signal: dict[str, Any]) -> str:
    return str(signal.get("id") or signal.get("url") or signal.get("title") or "").strip()


def _merge_match_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            key = ":".join(
                [
                    str(item.get("txid") or ""),
                    str(item.get("candidate_address") or ""),
                    str(item.get("address_role") or ""),
                ]
            )
            if key.strip(":"):
                rows[key] = item
    return sorted(rows.values(), key=lambda item: _safe_float(item.get("confidence")), reverse=True)


def _merge_news_signal_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    list_fields = ("candidate_addresses", "btc_addresses", "evm_addresses", "txids", "btc_amounts", "eth_amounts", "usd_amounts", "reasons")
    for group in groups:
        for signal in group:
            if not isinstance(signal, dict):
                continue
            key = _news_signal_key(signal)
            if not key:
                continue
            current = rows.setdefault(key, {})
            for field, value in signal.items():
                if field in (*list_fields, "large_transfer_matches"):
                    continue
                if value not in (None, "", [], {}):
                    current[field] = value
            current["id"] = current.get("id") or signal.get("id") or key
            current["confidence"] = max(_safe_float(current.get("confidence")), _safe_float(signal.get("confidence")))
            for field in list_fields:
                values = current.get(field) if isinstance(current.get(field), list) else []
                incoming = signal.get(field) if isinstance(signal.get(field), list) else []
                current[field] = list(dict.fromkeys([*values, *incoming]))
            current["large_transfer_matches"] = _merge_match_rows(
                current.get("large_transfer_matches") if isinstance(current.get("large_transfer_matches"), list) else [],
                signal.get("large_transfer_matches") if isinstance(signal.get("large_transfer_matches"), list) else [],
            )
    return sorted(
        rows.values(),
        key=lambda item: (_timestamp_ms(item.get("published_at")), _safe_float(item.get("confidence"))),
        reverse=True,
    )


def _merge_suspected_address_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            address = str(item.get("address") or "").strip()
            if not address:
                continue
            key = _address_key(address)
            current = rows.setdefault(key, {"address": address, "confidence": 0.0, "signals": [], "txids": [], "reasons": [], "latest_operations": []})
            current["address"] = current.get("address") or address
            current["confidence"] = max(_safe_float(current.get("confidence")), _safe_float(item.get("confidence")))
            for field in ("signals", "latest_operations"):
                values = current.get(field) if isinstance(current.get(field), list) else []
                incoming = item.get(field) if isinstance(item.get(field), list) else []
                if field == "signals":
                    current[field] = _merge_news_signal_rows(values, incoming)
                else:
                    current[field] = _merge_operation_rows(values, incoming)
            for field in ("txids", "reasons"):
                values = current.get(field) if isinstance(current.get(field), list) else []
                incoming = item.get(field) if isinstance(item.get(field), list) else []
                current[field] = list(dict.fromkeys([*values, *incoming]))[:20]
            current["signal_count"] = len(current.get("signals") if isinstance(current.get("signals"), list) else [])
    return sorted(rows.values(), key=lambda item: (_safe_float(item.get("confidence")), _safe_int(item.get("signal_count"))), reverse=True)


def _configured_chain_addresses(address_or_subject: str, config: dict[str, Any]) -> list[str]:
    values = list(extract_addresses(address_or_subject))
    for match in re.finditer(r"\b(?:bc1[ac-hj-np-z02-9]{11,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b", address_or_subject or "", re.IGNORECASE):
        values.append(match.group(0))
    for key in ("btc_addresses", "suspected_btc_addresses", "candidate_btc_addresses"):
        raw = config.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            text = str(item or "").strip()
            if text:
                values.append(text)
    unique: dict[str, str] = {}
    for value in values:
        key = _address_key(value)
        if key and key not in unique:
            unique[key] = value
    return list(unique.values())


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
    "hyperliquid": "Hyperliquid",
    "debank": "DeBank",
    "blackrock_free": "IBIT 免费监控",
    "btc_large_transfers": "BTC 大额底表",
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
                for secret_key in ("debank_access_key", "etherscan_api_key", "api_key"):
                    config[secret_key] = mask_secret(str(secret.get(secret_key) or ""))
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
            key = "translation_secret"
            secret = decrypt_json(self.state_get(key) or "", self.db.secret_key)
            api_key = str(config.pop("api_key", "") or "")
            if _should_store_secret(api_key):
                secret["api_key"] = api_key
                self.state_set(key, encrypt_json(secret, self.db.secret_key))
        if strategy_id == "whale":
            key = "whale_secret"
            secret = decrypt_json(self.state_get(key) or "", self.db.secret_key)
            for secret_key in ("debank_access_key", "etherscan_api_key", "api_key"):
                value = str(config.pop(secret_key, "") or "")
                if _should_store_secret(value):
                    secret[secret_key] = value
            if "api_key" in secret and "debank_access_key" not in secret:
                secret["debank_access_key"] = secret["api_key"]
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
                    config=_json_loads(row["config_json"], {}),
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
            INSERT INTO notifier_targets (id, name, type, enabled, secret_json, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                enabled = excluded.enabled,
                secret_json = excluded.secret_json,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (
                notifier.id,
                notifier.name,
                notifier.type,
                int(notifier.enabled),
                encrypted,
                json.dumps(dict(notifier.config or {}), ensure_ascii=False, sort_keys=True),
                created_at,
                now,
            ),
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

    def update_news_translation(
        self,
        event_id: int,
        translated_title: str,
        translated_summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if metadata is None:
            self.db.execute(
                """
                UPDATE news_events
                SET translated_title = ?, translated_summary = ?, last_seen_utc = ?
                WHERE id = ?
                """,
                (translated_title, translated_summary, utc_now_iso(), event_id),
            )
            return
        self.db.execute(
            """
            UPDATE news_events
            SET translated_title = ?, translated_summary = ?, metadata_json = ?, last_seen_utc = ?
            WHERE id = ?
            """,
            (translated_title, translated_summary, json.dumps(metadata, ensure_ascii=False, sort_keys=True), utc_now_iso(), event_id),
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

    def cleanup_btc_large_transfers(self, retention_days: int, *, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = (current.astimezone(timezone.utc) - timedelta(days=max(1, int(retention_days)))).isoformat()
        old_txids = [str(row["txid"]) for row in self.db.query("SELECT txid FROM btc_large_transfers WHERE block_time_utc < ?", (cutoff,))]
        if not old_txids:
            return 0
        for chunk in (old_txids[index:index + 500] for index in range(0, len(old_txids), 500)):
            placeholders = ",".join("?" for _ in chunk)
            self.db.execute(f"DELETE FROM btc_news_matches WHERE txid IN ({placeholders})", chunk)
            self.db.execute(f"DELETE FROM btc_large_transfers WHERE txid IN ({placeholders})", chunk)
        return len(old_txids)

    def upsert_btc_large_transfer(self, transfer: dict[str, Any]) -> bool:
        now = utc_now_iso()
        cursor = self.db.execute(
            """
            INSERT OR IGNORE INTO btc_large_transfers (
                txid, chain, asset, block_height, block_hash, block_time_utc,
                amount, amount_btc, total_input_amount, total_output_amount,
                fee_amount, total_input_btc, total_output_btc, fee_btc, input_addresses_json,
                output_addresses_json, address_operations_json, exchange_hints_json,
                source_url, raw_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(transfer.get("txid") or ""),
                str(transfer.get("chain") or "btc").lower(),
                str(transfer.get("asset") or "BTC").upper(),
                int(transfer.get("block_height") or 0),
                str(transfer.get("block_hash") or ""),
                str(transfer.get("block_time_utc") or ""),
                _safe_float(transfer.get("amount"), _safe_float(transfer.get("amount_btc"))),
                _safe_float(transfer.get("amount_btc")),
                _safe_float(transfer.get("total_input_amount"), _safe_float(transfer.get("total_input_btc"))),
                _safe_float(transfer.get("total_output_amount"), _safe_float(transfer.get("total_output_btc"))),
                _safe_float(transfer.get("fee_amount"), _safe_float(transfer.get("fee_btc"))),
                _safe_float(transfer.get("total_input_btc")),
                _safe_float(transfer.get("total_output_btc")),
                _safe_float(transfer.get("fee_btc")),
                json.dumps(transfer.get("input_addresses") or [], ensure_ascii=False, sort_keys=True),
                json.dumps(transfer.get("output_addresses") or [], ensure_ascii=False, sort_keys=True),
                json.dumps(transfer.get("address_operations") or [], ensure_ascii=False, sort_keys=True),
                json.dumps(transfer.get("exchange_hints") or [], ensure_ascii=False, sort_keys=True),
                str(transfer.get("source_url") or ""),
                json.dumps(transfer.get("raw") or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        return cursor.rowcount > 0

    def list_btc_large_transfers(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        min_btc: float | None = None,
        query: str = "",
        matched_only: bool = False,
    ) -> BtcLargeTransferListOut:
        where: list[str] = []
        params: list[Any] = []
        if min_btc is not None and min_btc > 0:
            where.append("amount >= ?")
            params.append(float(min_btc))
        query_text = query.strip()
        if query_text:
            where.append("(txid LIKE ? OR chain LIKE ? OR asset LIKE ? OR input_addresses_json LIKE ? OR output_addresses_json LIKE ?)")
            like = f"%{query_text}%"
            params.extend([like, like, like, like, like])
        if matched_only:
            where.append("EXISTS (SELECT 1 FROM btc_news_matches WHERE btc_news_matches.txid = btc_large_transfers.txid)")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS count FROM btc_large_transfers {where_sql}", params)
        rows = self.db.query(
            f"""
            SELECT btc_large_transfers.*,
                   (SELECT COUNT(*) FROM btc_news_matches WHERE btc_news_matches.txid = btc_large_transfers.txid) AS match_count
            FROM btc_large_transfers
            {where_sql}
            ORDER BY block_time_utc DESC, amount DESC
            LIMIT ? OFFSET ?
            """,
            (*params, max(1, min(int(limit), 200)), max(0, int(offset))),
        )
        return BtcLargeTransferListOut(
            items=[self._btc_transfer_from_row(row, include_matches=False) for row in rows],
            total=int(total["count"] if total else 0),
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )

    def get_btc_large_transfer(self, txid: str) -> BtcLargeTransferOut | None:
        row = self.db.query_one(
            """
            SELECT btc_large_transfers.*,
                   (SELECT COUNT(*) FROM btc_news_matches WHERE btc_news_matches.txid = btc_large_transfers.txid) AS match_count
            FROM btc_large_transfers
            WHERE txid = ?
            """,
            (txid,),
        )
        if row is None:
            return None
        return self._btc_transfer_from_row(row, include_matches=True)

    def list_chain_address_operations(
        self,
        addresses: list[str],
        *,
        lookback_days: int = 90,
        limit_per_address: int = 30,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized: dict[str, str] = {}
        for address in addresses:
            text = str(address or "").strip()
            key = _address_key(text)
            if key and key not in normalized:
                normalized[key] = text
        if not normalized:
            return {}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))).isoformat()
        address_tokens = list(normalized.values())[:200]
        clauses: list[str] = []
        params: list[Any] = [cutoff]
        for address in address_tokens:
            token_values = {address, address.lower()}
            for token in token_values:
                like = f"%{token}%"
                clauses.append("(address_operations_json LIKE ? OR input_addresses_json LIKE ? OR output_addresses_json LIKE ?)")
                params.extend([like, like, like])
        if not clauses:
            return {}

        row_limit = max(100, min(5000, len(address_tokens) * max(1, int(limit_per_address)) * 4))
        rows = self.db.query(
            f"""
            SELECT btc_large_transfers.*,
                   (SELECT COUNT(*) FROM btc_news_matches WHERE btc_news_matches.txid = btc_large_transfers.txid) AS match_count
            FROM btc_large_transfers
            WHERE block_time_utc >= ?
              AND ({" OR ".join(clauses)})
            ORDER BY block_time_utc DESC, amount DESC
            LIMIT ?
            """,
            (*params, row_limit),
        )

        result: dict[str, list[dict[str, Any]]] = {address: [] for address in normalized.values()}
        for row in rows:
            transfer = self._btc_transfer_from_row(row, include_matches=False)
            asset = str(transfer.asset or "BTC").upper()
            chain = str(transfer.chain or ("eth" if asset == "ETH" else "btc")).lower()
            input_addresses = transfer.input_addresses
            output_addresses = transfer.output_addresses
            timestamp_ms = _timestamp_ms(transfer.block_time_utc)
            for operation in transfer.address_operations:
                if not isinstance(operation, dict):
                    continue
                original = normalized.get(_address_key(operation.get("address")))
                if not original:
                    continue
                item = dict(operation)
                amount = _safe_float(item.get("amount"), _safe_float(item.get("amount_btc"), _safe_float(item.get("amount_eth"), transfer.amount)))
                item["address"] = str(operation.get("address") or original)
                item["txid"] = transfer.txid
                item["chain"] = chain
                item["asset"] = asset
                item["amount"] = amount
                if asset == "ETH":
                    item["amount_eth"] = _safe_float(item.get("amount_eth"), amount)
                    item.setdefault("net_eth", item.get("amount_eth"))
                else:
                    item["amount_btc"] = _safe_float(item.get("amount_btc"), amount)
                    item.setdefault("net_btc", item.get("amount_btc"))
                item["block_height"] = transfer.block_height
                item["block_hash"] = transfer.block_hash
                item["timestamp"] = transfer.block_time_utc
                item["timestamp_ms"] = _safe_int(item.get("timestamp_ms"), timestamp_ms)
                item["source_url"] = transfer.source_url
                item["confirmed"] = True
                item["input_counterparties"] = [entry for entry in input_addresses if _address_key(entry.get("address")) != _address_key(original)][:20]
                item["output_counterparties"] = [entry for entry in output_addresses if _address_key(entry.get("address")) != _address_key(original)][:20]
                item["from_persisted_bottom_table"] = True
                result[original].append(item)

        return {
            address: _merge_operation_rows(rows)[: max(1, int(limit_per_address))]
            for address, rows in result.items()
            if rows
        }

    def btc_large_transfer_stats(self, *, min_btc: float = 500.0, min_eth: float = 5000.0) -> BtcLargeTransferStatsOut:
        today = datetime.now(timezone.utc).date().isoformat()
        total = self.db.query_one("SELECT COUNT(*) AS count FROM btc_large_transfers")
        today_count = self.db.query_one("SELECT COUNT(*) AS count FROM btc_large_transfers WHERE block_time_utc >= ?", (today,))
        latest = self.db.query_one("SELECT block_height, block_time_utc FROM btc_large_transfers WHERE chain = 'btc' ORDER BY block_height DESC LIMIT 1")
        latest_eth = self.db.query_one("SELECT block_height, block_time_utc FROM btc_large_transfers WHERE chain = 'eth' ORDER BY block_height DESC LIMIT 1")
        matched = self.db.query_one("SELECT COUNT(DISTINCT txid) AS count FROM btc_news_matches")
        scanned_height = _safe_int(self.state_get("btc_large_transfers:last_scanned_height"))
        eth_scanned_height = _safe_int(self.state_get("eth_large_transfers:last_scanned_height"))
        return BtcLargeTransferStatsOut(
            total=int(total["count"] if total else 0),
            today_count=int(today_count["count"] if today_count else 0),
            latest_block_height=int(latest["block_height"]) if latest else None,
            latest_eth_block_height=int(latest_eth["block_height"]) if latest_eth else None,
            latest_scanned_height=scanned_height if scanned_height > 0 else None,
            latest_eth_scanned_height=eth_scanned_height if eth_scanned_height > 0 else None,
            latest_scan_time=self.state_get("btc_large_transfers:last_scan_at"),
            latest_eth_scan_time=self.state_get("eth_large_transfers:last_scan_at"),
            min_btc=float(min_btc),
            min_eth=float(min_eth),
            matched_count=int(matched["count"] if matched else 0),
        )

    def list_btc_large_transfer_candidates(
        self,
        *,
        start_utc: str,
        end_utc: str,
        min_btc: float,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows = self.db.query(
            """
            SELECT btc_large_transfers.*,
                   (SELECT COUNT(*) FROM btc_news_matches WHERE btc_news_matches.txid = btc_large_transfers.txid) AS match_count
            FROM btc_large_transfers
            WHERE block_time_utc BETWEEN ? AND ?
              AND amount >= ?
            ORDER BY amount DESC, block_time_utc DESC
            LIMIT ?
            """,
            (start_utc, end_utc, float(min_btc), max(1, min(int(limit), 1000))),
        )
        return [self._btc_transfer_from_row(row, include_matches=False).model_dump() for row in rows]

    def save_btc_news_matches(self, target_id: str, signals: list[dict[str, Any]]) -> int:
        inserted = 0
        now = utc_now_iso()
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            signal_id = str(signal.get("id") or "")
            matches = signal.get("large_transfer_matches") if isinstance(signal.get("large_transfer_matches"), list) else []
            if not signal_id:
                continue
            self.db.execute("DELETE FROM btc_news_matches WHERE target_id = ? AND signal_id = ?", (target_id, signal_id))
            if not matches:
                continue
            for match in matches:
                if not isinstance(match, dict):
                    continue
                txid = str(match.get("txid") or "")
                address = str(match.get("candidate_address") or "")
                role = str(match.get("address_role") or "unknown")
                if not txid or not address:
                    continue
                cursor = self.db.execute(
                    """
                    INSERT INTO btc_news_matches (
                        target_id, signal_id, txid, candidate_address, address_role,
                        confidence, reasons_json, signal_json, transfer_json,
                        published_at_utc, matched_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(target_id, signal_id, txid, candidate_address, address_role)
                    DO UPDATE SET
                        confidence = excluded.confidence,
                        reasons_json = excluded.reasons_json,
                        signal_json = excluded.signal_json,
                        transfer_json = excluded.transfer_json,
                        published_at_utc = excluded.published_at_utc,
                        matched_at_utc = excluded.matched_at_utc
                    """,
                    (
                        target_id,
                        signal_id,
                        txid,
                        address,
                        role,
                        float(match.get("confidence") or 0),
                        json.dumps(match.get("reasons") or [], ensure_ascii=False, sort_keys=True),
                        json.dumps(signal, ensure_ascii=False, sort_keys=True),
                        json.dumps(match.get("transfer") or {}, ensure_ascii=False, sort_keys=True),
                        str(signal.get("published_at") or ""),
                        now,
                    ),
                )
                inserted += int(cursor.rowcount > 0)
        return inserted

    def list_persisted_ibit_news_signals(
        self,
        target_id: str,
        *,
        lookback_days: int = 90,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))).isoformat()
        rows = self.db.query(
            """
            SELECT * FROM btc_news_matches
            WHERE target_id = ?
              AND published_at_utc >= ?
            ORDER BY published_at_utc DESC, confidence DESC, id DESC
            LIMIT ?
            """,
            (target_id, cutoff, max(1, min(int(limit) * 20, 5000))),
        )
        by_signal: dict[str, dict[str, Any]] = {}
        for row in rows:
            signal = _json_loads(row["signal_json"], {})
            if not isinstance(signal, dict):
                signal = {}
            signal_id = str(signal.get("id") or row["signal_id"] or "").strip()
            if not signal_id:
                continue
            current = by_signal.setdefault(signal_id, dict(signal))
            current["id"] = current.get("id") or signal_id
            current["published_at"] = current.get("published_at") or row["published_at_utc"]
            current["confidence"] = max(_safe_float(current.get("confidence")), _safe_float(row["confidence"]))

            transfer = _json_loads(row["transfer_json"], {})
            if not isinstance(transfer, dict):
                transfer = {}
            asset = str(transfer.get("asset") or ("ETH" if str(transfer.get("chain") or "").lower() == "eth" else "BTC")).upper()
            address_value = _safe_float(transfer.get("amount"), _safe_float(transfer.get("amount_btc")))
            match = {
                "txid": row["txid"],
                "chain": str(transfer.get("chain") or ("eth" if asset == "ETH" else "btc")).lower(),
                "asset": asset,
                "candidate_address": row["candidate_address"],
                "address_role": row["address_role"],
                "address_value": address_value,
                "address_value_btc": address_value if asset == "BTC" else None,
                "address_value_eth": address_value if asset == "ETH" else None,
                "confidence": _safe_float(row["confidence"]),
                "reasons": _json_loads(row["reasons_json"], []),
                "behavior": "转出" if str(row["address_role"]) in {"source", "input"} else "转入",
                "source_url": str(transfer.get("source_url") or ""),
                "transfer": transfer,
            }
            current["large_transfer_matches"] = _merge_match_rows(
                current.get("large_transfer_matches") if isinstance(current.get("large_transfer_matches"), list) else [],
                [match],
            )
            current["candidate_addresses"] = list(dict.fromkeys([*(current.get("candidate_addresses") if isinstance(current.get("candidate_addresses"), list) else []), row["candidate_address"]]))
            current["txids"] = list(dict.fromkeys([*(current.get("txids") if isinstance(current.get("txids"), list) else []), row["txid"]]))
            reasons = current.get("reasons") if isinstance(current.get("reasons"), list) else []
            current["reasons"] = list(dict.fromkeys([*reasons, *_json_loads(row["reasons_json"], [])]))

        return _merge_news_signal_rows(list(by_signal.values()))[: max(1, int(limit))]

    def confirm_btc_address_for_target(self, target_id: str, address: str, *, role: str = "candidate", label: str | None = None) -> WhaleTargetOut:
        row = self.db.query_one("SELECT * FROM whale_targets WHERE id = ?", (target_id,))
        if row is None:
            raise KeyError(target_id)
        address_text = address.strip()
        if not address_text:
            raise ValueError("empty address")
        config = _json_loads(row["config_json"], {})
        key = "btc_addresses" if role == "confirmed" else "suspected_btc_addresses"
        values = [str(item).strip() for item in config.get(key, []) if str(item).strip()]
        lowered = {item.lower() for item in values}
        if address_text.lower() not in lowered:
            values.append(address_text)
        config[key] = values
        label_text = str(label or "").strip()
        if label_text:
            labels = config.get("btc_address_labels")
            if not isinstance(labels, dict):
                labels = {}
            labels[address_text] = label_text
            config["btc_address_labels"] = labels
        self.db.execute(
            "UPDATE whale_targets SET config_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(config, ensure_ascii=False, sort_keys=True), utc_now_iso(), target_id),
        )
        target = self.get_whale_target(target_id)
        if target is None:
            raise KeyError(target_id)
        return target

    def _btc_transfer_from_row(self, row: Any, *, include_matches: bool) -> BtcLargeTransferOut:
        txid = str(row["txid"])
        matches = []
        if include_matches:
            match_rows = self.db.query(
                """
                SELECT * FROM btc_news_matches
                WHERE txid = ?
                ORDER BY confidence DESC, published_at_utc DESC
                LIMIT 30
                """,
                (txid,),
            )
            matches = [
                {
                    "target_id": match["target_id"],
                    "signal_id": match["signal_id"],
                    "candidate_address": match["candidate_address"],
                    "address_role": match["address_role"],
                    "confidence": float(match["confidence"] or 0),
                    "reasons": _json_loads(match["reasons_json"], []),
                    "signal": _json_loads(match["signal_json"], {}),
                    "transfer": _json_loads(match["transfer_json"], {}),
                    "published_at_utc": match["published_at_utc"],
                    "matched_at_utc": match["matched_at_utc"],
                }
                for match in match_rows
            ]
        return BtcLargeTransferOut(
            txid=txid,
            chain=str(row["chain"] or "btc"),
            asset=str(row["asset"] or "BTC"),
            block_height=int(row["block_height"]),
            block_hash=row["block_hash"],
            block_time_utc=row["block_time_utc"],
            amount=_safe_float(row["amount"], _safe_float(row["amount_btc"])),
            amount_btc=float(row["amount_btc"] or 0),
            total_input_amount=_safe_float(row["total_input_amount"], _safe_float(row["total_input_btc"])),
            total_output_amount=_safe_float(row["total_output_amount"], _safe_float(row["total_output_btc"])),
            fee_amount=_safe_float(row["fee_amount"], _safe_float(row["fee_btc"])),
            total_input_btc=float(row["total_input_btc"] or 0),
            total_output_btc=float(row["total_output_btc"] or 0),
            fee_btc=float(row["fee_btc"] or 0),
            input_addresses=_json_loads(row["input_addresses_json"], []),
            output_addresses=_json_loads(row["output_addresses_json"], []),
            address_operations=_json_loads(row["address_operations_json"], []),
            exchange_hints=_json_loads(row["exchange_hints_json"], []),
            source_url=row["source_url"],
            raw=_json_loads(row["raw_json"], {}),
            match_count=int(row["match_count"] or 0) if "match_count" in row.keys() else len(matches),
            matches=matches,
            created_at=row["created_at"],
        )

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

    def get_whale_secret(self) -> dict[str, Any]:
        secret = decrypt_json(self.state_get("whale_secret") or "", self.db.secret_key)
        if "api_key" in secret and "debank_access_key" not in secret:
            secret["debank_access_key"] = secret["api_key"]
        return secret

    def list_whale_targets(self) -> list[WhaleTargetOut]:
        rows = self.db.query("SELECT * FROM whale_targets ORDER BY updated_at DESC, label ASC")
        result: list[WhaleTargetOut] = []
        for row in rows:
            config = _json_loads(row["config_json"], {})
            snapshot = self.get_whale_snapshot(str(row["id"]))
            if snapshot:
                config = {
                    **config,
                    **{
                        key: snapshot.get(key)
                        for key in (
                            "positions",
                            "holdings",
                            "defi_positions",
                            "open_orders",
                            "fills",
                            "historical_orders",
                            "funding",
                            "ledger_updates",
                            "portfolio",
                            "account_summary",
                            "source_status",
                        )
                    },
                }
                config["last_snapshot_at"] = snapshot.get("updated_at")
                account = snapshot.get("account_summary") if isinstance(snapshot.get("account_summary"), dict) else {}
                config["current_operation_amount"] = (
                    account.get("contract_notional")
                    or account.get("blackrock_last_flow_usd")
                    or account.get("blackrock_official_net_assets")
                    or account.get("total_balance")
                    or config.get("current_operation_amount")
                )
            result.append(
                WhaleTargetOut(
                    id=row["id"],
                    label=row["label"],
                    address_or_subject=row["address_or_subject"],
                    enabled=bool(row["enabled"]),
                    config=config,
                    updated_at=row["updated_at"],
                )
            )
        return result

    def upsert_whale_target(self, item: WhaleTargetUpsert) -> WhaleTargetOut:
        config = dict(item.config or {})
        addresses = list(dict.fromkeys([address.lower() for address in extract_addresses(item.address_or_subject)] + [str(address).lower() for address in config.get("addresses", []) if re.fullmatch(r"0x[a-fA-F0-9]{40}", str(address))]))
        if addresses:
            config["addresses"] = addresses
        target_id = _slugify(item.id or (addresses[0] if addresses else item.label or item.address_or_subject))
        now = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO whale_targets (id, label, address_or_subject, enabled, config_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                label = excluded.label,
                address_or_subject = excluded.address_or_subject,
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (target_id, item.label.strip() or target_id, item.address_or_subject.strip(), int(item.enabled), json.dumps(config, ensure_ascii=False, sort_keys=True), now),
        )
        target = self.get_whale_target(target_id)
        if target is None:
            raise KeyError(target_id)
        return target

    def delete_whale_target(self, target_id: str) -> None:
        self.db.execute("DELETE FROM whale_targets WHERE id = ?", (target_id,))
        self.db.execute("DELETE FROM whale_snapshots WHERE target_id = ?", (target_id,))
        self.db.execute("DELETE FROM whale_events WHERE target_id = ?", (target_id,))
        self.db.execute("DELETE FROM app_state WHERE state_key LIKE ?", (f"whale:hyperliquid:fills:{target_id}:%",))

    def get_whale_target(self, target_id: str) -> WhaleTargetOut | None:
        return next((item for item in self.list_whale_targets() if item.id == target_id), None)

    def resolve_whale_addresses(self, query: str) -> WhaleAddressResolveResponse:
        targets = [
            {
                "id": item.id,
                "label": item.label,
                "address_or_subject": item.address_or_subject,
                "config": item.config,
            }
            for item in self.list_whale_targets()
        ]
        candidates = [WhaleAddressCandidate(**item) for item in resolve_address_candidates(query, targets)]
        return WhaleAddressResolveResponse(query=query, candidates=candidates)

    def get_whale_snapshot(self, target_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM whale_snapshots WHERE target_id = ?", (target_id,))
        if row is None:
            return {}
        snapshot = _json_loads(row["snapshot_json"], {})
        if isinstance(snapshot, dict):
            snapshot["updated_at"] = row["updated_at"]
            return snapshot
        return {}

    def save_whale_snapshot(self, target_id: str, snapshot: dict[str, Any]) -> None:
        now = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO whale_snapshots (target_id, snapshot_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                snapshot_json = excluded.snapshot_json,
                updated_at = excluded.updated_at
            """,
            (target_id, json.dumps(snapshot, ensure_ascii=False, sort_keys=True), now),
        )

    def add_whale_event(
        self,
        *,
        provider: str,
        target_id: str,
        action_type: str,
        summary: str,
        payload: dict[str, Any],
        occurred_at_utc: str | None = None,
        event_key: str | None = None,
        notification_required: bool = False,
    ) -> int:
        now = utc_now_iso()
        cursor = self.db.execute(
            """
            INSERT OR IGNORE INTO whale_events (
                provider, target_id, action_type, event_key, summary, payload_json,
                occurred_at_utc, created_at, notification_required, notification_sent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                target_id,
                action_type,
                event_key,
                summary,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                occurred_at_utc or now,
                now,
                int(notification_required),
                0 if notification_required else 1,
            ),
        )
        if cursor.rowcount == 0 and event_key:
            existing = self.db.query_one("SELECT id FROM whale_events WHERE event_key = ?", (event_key,))
            if existing:
                return int(existing["id"])
        return int(cursor.lastrowid)

    def list_pending_whale_notifications(self):
        return self.db.query(
            """
            SELECT whale_events.*, whale_targets.label AS target_label, whale_targets.address_or_subject, whale_targets.config_json AS target_config_json
            FROM whale_events
            LEFT JOIN whale_targets ON whale_events.target_id = whale_targets.id
            WHERE whale_events.notification_required = 1 AND whale_events.notification_sent = 0
            ORDER BY whale_events.occurred_at_utc ASC, whale_events.id ASC
            LIMIT 30
            """
        )

    def mark_whale_notification(self, event_id: int, *, ok: bool, error: str | None = None) -> None:
        self.db.execute(
            """
            UPDATE whale_events
            SET notification_sent = CASE WHEN ? THEN 1 ELSE notification_sent END,
                notification_attempts = notification_attempts + 1,
                last_notification_error = ?
            WHERE id = ?
            """,
            (int(ok), None if ok else (error or "")[:1000], event_id),
        )

    def _enrich_ibit_snapshot_from_bottom_table(self, target: WhaleTargetOut, snapshot: dict[str, Any]) -> dict[str, Any]:
        if str(target.config.get("provider") or "").strip().lower() != "blackrock_free_monitor":
            return snapshot
        addresses = _configured_chain_addresses(target.address_or_subject, target.config)
        if not addresses:
            return snapshot
        confirmed_addresses = _configured_chain_addresses(target.address_or_subject, {"btc_addresses": target.config.get("btc_addresses", [])})

        strategy = self.get_strategy("whale")
        strategy_config = strategy.config if strategy is not None else {}
        lookback_days = _safe_int(target.config.get("btc_candidate_retention_days"), _safe_int(strategy_config.get("btc_candidate_retention_days"), 90))
        persisted = self.list_chain_address_operations(addresses, lookback_days=lookback_days, limit_per_address=30)
        persisted_signals = self.list_persisted_ibit_news_signals(target.id, lookback_days=lookback_days, limit=120)
        if not persisted and not persisted_signals:
            return snapshot

        raw = snapshot.setdefault("raw", {})
        if not isinstance(raw, dict):
            raw = {}
            snapshot["raw"] = raw
        cluster = raw.setdefault("btc_cluster", {})
        if not isinstance(cluster, dict):
            cluster = {}
            raw["btc_cluster"] = cluster
        news = raw.setdefault("news_signals", {})
        if not isinstance(news, dict):
            news = {}
            raw["news_signals"] = news

        cluster_addresses = cluster.get("addresses") if isinstance(cluster.get("addresses"), list) else []
        cluster["addresses"] = list(dict.fromkeys([*cluster_addresses, *confirmed_addresses]))

        activity = cluster.get("address_activity") if isinstance(cluster.get("address_activity"), dict) else {}
        confirmed_activity = news.get("confirmed_address_activity") if isinstance(news.get("confirmed_address_activity"), dict) else {}
        suspected_activity = news.get("suspected_address_activity") if isinstance(news.get("suspected_address_activity"), dict) else {}
        merged_activity: dict[str, list[dict[str, Any]]] = {}
        merged_confirmed: dict[str, list[dict[str, Any]]] = {}
        merged_suspected: dict[str, list[dict[str, Any]]] = {}

        def existing_rows(source: dict[str, Any], address: str) -> list[dict[str, Any]]:
            for key, value in source.items():
                if _address_key(key) == _address_key(address) and isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            return []

        for address, operations in persisted.items():
            merged = _merge_operation_rows(existing_rows(activity, address), operations)
            merged_activity[address] = merged
            merged_confirmed[address] = _merge_operation_rows(existing_rows(confirmed_activity, address), operations)
            merged_suspected[address] = _merge_operation_rows(existing_rows(suspected_activity, address), operations)

        for key, value in activity.items():
            if isinstance(value, list) and not any(_address_key(key) == _address_key(address) for address in merged_activity):
                merged_activity[str(key)] = [item for item in value if isinstance(item, dict)]
        cluster["address_activity"] = merged_activity
        news["confirmed_address_activity"] = {**confirmed_activity, **merged_confirmed}
        news["suspected_address_activity"] = {**suspected_activity, **merged_suspected}

        current_signals = news.get("signals") if isinstance(news.get("signals"), list) else []
        merged_signals = _merge_news_signal_rows(current_signals, persisted_signals)
        news["signals"] = merged_signals
        current_suspected = news.get("suspected_addresses") if isinstance(news.get("suspected_addresses"), list) else []
        rebuilt_suspected = BlackRockFreeAddressBuilder.build_suspected_addresses(
            merged_signals,
            candidate_activity={**news["confirmed_address_activity"], **news["suspected_address_activity"]},
        )
        news["suspected_addresses"] = _merge_suspected_address_rows(current_suspected, rebuilt_suspected)
        news["btc_large_transfer_match_count"] = sum(
            len(signal.get("large_transfer_matches") if isinstance(signal.get("large_transfer_matches"), list) else [])
            for signal in merged_signals
        )

        account = snapshot.setdefault("account_summary", {})
        if not isinstance(account, dict):
            account = {}
            snapshot["account_summary"] = account
        account["blackrock_btc_cluster_address_count"] = len(confirmed_addresses)
        account["blackrock_btc_cluster_operation_count"] = sum(len(rows) for rows in merged_activity.values())
        account["blackrock_btc_cluster_persisted_operation_count"] = sum(len(rows) for rows in persisted.values())
        account["ibit_news_candidate_count"] = len(merged_signals)
        account["ibit_suspected_address_count"] = len(news["suspected_addresses"])
        return snapshot

    def get_whale_detail(self, target_id: str) -> WhaleDetailOut | None:
        target = self.get_whale_target(target_id)
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
        snapshot = self.get_whale_snapshot(target_id)
        snapshot = self._enrich_ibit_snapshot_from_bottom_table(target, snapshot)
        def list_field(name: str) -> list[dict[str, Any]]:
            return list(snapshot.get(name) or config.get(name) or [])

        return WhaleDetailOut(
            target=target,
            recent_events=recent_events,
            positions=list_field("positions"),
            holdings=list_field("holdings"),
            defi_positions=list_field("defi_positions"),
            open_orders=list_field("open_orders"),
            fills=list_field("fills"),
            historical_orders=list_field("historical_orders"),
            funding=list_field("funding"),
            ledger_updates=list_field("ledger_updates"),
            portfolio=list_field("portfolio"),
            account_summary=dict(snapshot.get("account_summary") or config.get("account_summary", {})),
            snapshot=snapshot,
            updated_at=str(snapshot.get("updated_at")) if snapshot.get("updated_at") else None,
        )
