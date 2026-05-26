from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from .events import EventBus
from .store import Store
from .whale import DeBankProvider, HyperliquidProvider, WhaleProviderError, extract_addresses, merge_snapshots


LOGGER = logging.getLogger("market_monitor.whale")


class WhaleRunner:
    def __init__(self, store: Store, bus: EventBus, timeout_seconds: int = 20) -> None:
        self.store = store
        self.bus = bus
        self.timeout_seconds = timeout_seconds

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                LOGGER.exception("巨鲸轮询失败")
            await asyncio.sleep(self._next_sleep())

    def _next_sleep(self) -> int:
        strategy = self.store.get_strategy("whale")
        if not strategy:
            return 300
        try:
            poll_seconds = int(strategy.config.get("poll_seconds", 180))
            if bool(strategy.config.get("trade_monitor_enabled", True)):
                poll_seconds = min(poll_seconds, int(strategy.config.get("trade_poll_seconds", 60)))
            return max(60, poll_seconds)
        except (TypeError, ValueError):
            return 180

    def run_once(self) -> None:
        strategy = self.store.get_strategy("whale")
        if strategy is None or not strategy.enabled or not bool(strategy.config.get("enabled", True)):
            return
        config = dict(strategy.config)
        secret = self.store.get_whale_secret()
        targets = [target for target in self.store.list_whale_targets() if target.enabled]
        for target in targets:
            addresses = _target_addresses(target.address_or_subject, target.config)
            if not addresses:
                self.store.save_whale_snapshot(
                    target.id,
                    {
                        "positions": [],
                        "holdings": [],
                        "defi_positions": [],
                        "open_orders": [],
                        "account_summary": {},
                        "source_status": {"address": {"ok": False, "message": "未配置完整 EVM 地址"}},
                    },
                )
                continue
            try:
                self._sync_target(target, addresses, config, secret)
            except Exception:  # noqa: BLE001
                LOGGER.exception("巨鲸目标同步失败 target=%s", target.id)

    def _sync_target(self, target: Any, addresses: list[str], config: dict[str, Any], secret: dict[str, Any]) -> None:
        target_id = str(target.id)
        old_snapshot = self.store.get_whale_snapshot(target_id)
        snapshots = []
        source_status: dict[str, Any] = {}
        for address in addresses:
            if bool(config.get("hyperliquid_enabled", True)):
                provider = HyperliquidProvider(str(config.get("hyperliquid_base_url") or "https://api.hyperliquid.xyz"), self.timeout_seconds)
                try:
                    include_extended = self._should_refresh_extended(target.id, address, config)
                    snapshot = provider.fetch(address, include_extended=include_extended)
                    if not include_extended:
                        _preserve_extended_snapshot_fields(snapshot, old_snapshot)
                    snapshot.account_summary["address"] = address
                    snapshots.append(snapshot)
                    if bool(config.get("trade_monitor_enabled", True)):
                        self._sync_fills(target, address, provider, snapshot.fills, config, current_positions=snapshot.positions, old_positions=old_snapshot.get("positions", []))
                    if include_extended:
                        self._mark_extended_refreshed(target.id, address)
                    self.store.record_source_success(provider.source_name, provider.label)
                except WhaleProviderError as exc:
                    source_status[provider.source_name] = {"ok": False, "message": str(exc)}
                    self.store.record_source_error(provider.source_name, provider.label, str(exc))
            if bool(config.get("debank_enabled", False)):
                provider = DeBankProvider(
                    str(config.get("debank_base_url") or "https://pro-openapi.debank.com"),
                    str(secret.get("debank_access_key") or secret.get("api_key") or ""),
                    self.timeout_seconds,
                )
                try:
                    snapshot = provider.fetch(address)
                    snapshot.account_summary["address"] = address
                    snapshots.append(snapshot)
                    if snapshot.source_status.get("debank", {}).get("ok"):
                        self.store.record_source_success(provider.source_name, provider.label)
                except WhaleProviderError as exc:
                    source_status[provider.source_name] = {"ok": False, "message": str(exc)}
                    self.store.record_source_error(provider.source_name, provider.label, str(exc))
        merged = merge_snapshots(snapshots)
        merged["source_status"] = {**merged.get("source_status", {}), **source_status}
        self._record_events(target_id, old_snapshot, merged, config)
        self.store.save_whale_snapshot(target_id, merged)
        if self.bus is not None:
            self.bus.publish_threadsafe("whale", {"target_id": target_id})

    def _should_refresh_extended(self, target_id: str, address: str, config: dict[str, Any]) -> bool:
        interval = max(300, _int(config.get("extended_poll_seconds"), 900))
        last = _int(self.store.state_get(_extended_cursor_key(target_id, address)))
        return last <= 0 or _now_ms() - last >= interval * 1000

    def _mark_extended_refreshed(self, target_id: str, address: str) -> None:
        self.store.state_set(_extended_cursor_key(target_id, address), str(_now_ms()))

    def _sync_fills(
        self,
        target: Any,
        address: str,
        provider: HyperliquidProvider,
        recent_fills: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        current_positions: list[dict[str, Any]] | None = None,
        old_positions: list[dict[str, Any]] | None = None,
    ) -> None:
        cursor_key = _fill_cursor_key(str(target.id), address)
        now_ms = _now_ms()
        cursor = _load_cursor(self.store.state_get(cursor_key))
        if cursor is None:
            start_ms = _initial_fill_start_ms(config, now_ms)
            if start_ms is None:
                latest_time = max([_int(fill.get("timestamp")) for fill in recent_fills], default=now_ms)
                self.store.state_set(cursor_key, json.dumps({"last_time_ms": latest_time}, sort_keys=True))
                return
            fills = self._fetch_incremental_fills(provider, address, start_ms, now_ms)
            self._record_fill_events(
                target,
                address,
                fills,
                config,
                notify_allowed=str(config.get("initial_fill_sync_mode") or "cursor_only") != "cursor_only",
                current_positions=current_positions,
                old_positions=old_positions,
            )
            latest_time = max([_int(fill.get("timestamp")) for fill in fills], default=now_ms)
            self.store.state_set(cursor_key, json.dumps({"last_time_ms": latest_time}, sort_keys=True))
            return

        start_ms = max(0, int(cursor.get("last_time_ms", 0)) + 1)
        if start_ms > now_ms:
            return
        fills = self._fetch_incremental_fills(provider, address, start_ms, now_ms)
        self._record_fill_events(
            target,
            address,
            fills,
            config,
            notify_allowed=True,
            current_positions=current_positions,
            old_positions=old_positions,
        )
        latest_time = max([_int(fill.get("timestamp")) for fill in fills], default=int(cursor.get("last_time_ms", 0)))
        self.store.state_set(cursor_key, json.dumps({"last_time_ms": max(latest_time, int(cursor.get("last_time_ms", 0)))}, sort_keys=True))

    def _fetch_incremental_fills(self, provider: HyperliquidProvider, address: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        fills: list[dict[str, Any]] = []
        cursor = start_ms
        for _ in range(5):
            page = provider.fetch_fills_by_time(address, cursor, end_ms)
            if not page:
                break
            page_sorted = sorted(page, key=lambda item: _int(item.get("timestamp")))
            fills.extend(page_sorted)
            max_time = max(_int(item.get("timestamp")) for item in page_sorted)
            if max_time <= cursor or len(page_sorted) < 2000:
                break
            cursor = max_time + 1
        deduped: dict[str, dict[str, Any]] = {}
        for fill in fills:
            deduped[_fill_identity(fill)] = fill
        return sorted(deduped.values(), key=lambda item: _int(item.get("timestamp")))

    def _record_fill_events(
        self,
        target: Any,
        address: str,
        fills: list[dict[str, Any]],
        config: dict[str, Any],
        *,
        notify_allowed: bool,
        current_positions: list[dict[str, Any]] | None = None,
        old_positions: list[dict[str, Any]] | None = None,
    ) -> None:
        if not bool(config.get("trade_notification_enabled", True)):
            notify_allowed = False
        for fill in sorted(fills, key=lambda item: _int(item.get("timestamp"))):
            is_large = _is_large_fill(fill, config)
            fill_context = _fill_event_context(fill, current_positions or [], old_positions or [])
            coin = str(fill.get("coin") or "")
            side = str(fill.get("side") or "")
            size = _float(fill.get("size"))
            price = _float(fill.get("price"))
            notional = _float(fill.get("notional"))
            summary = f"{target.label} {side}{coin} {size:g} @ ${price:,.2f}，成交额 ${notional:,.0f}"
            self.store.add_whale_event(
                provider="hyperliquid",
                target_id=str(target.id),
                action_type=("large_trade_buy" if str(fill.get("side_code")) == "B" else "large_trade_sell") if is_large else ("trade_buy" if str(fill.get("side_code")) == "B" else "trade_sell"),
                summary=summary,
                payload={
                    "target_label": target.label,
                    "address": address,
                    "fill": fill,
                    "is_large": is_large,
                    "hyperdash_url": f"https://hyperdash.info/trader/{address}",
                    **fill_context,
                },
                occurred_at_utc=_fill_time_iso(fill),
                event_key=f"hyperliquid:{target.id}:{address.lower()}:{_fill_identity(fill)}",
                notification_required=notify_allowed,
            )

    def _record_events(self, target_id: str, old_snapshot: dict[str, Any], new_snapshot: dict[str, Any], config: dict[str, Any]) -> None:
        threshold_pct = _float(config.get("position_change_alert_pct"), 25.0)
        min_value = _float(config.get("min_position_value_usd"), 10000.0)
        liquidation_distance = _float(config.get("liquidation_distance_pct"), 5.0)
        old_positions = {_position_key(item): item for item in old_snapshot.get("positions", []) if isinstance(item, dict)}
        new_positions = {_position_key(item): item for item in new_snapshot.get("positions", []) if isinstance(item, dict)}
        for key, position in new_positions.items():
            notional = abs(_float(position.get("notional")))
            if notional < min_value:
                continue
            old = old_positions.get(key)
            if old is None:
                self.store.add_whale_event(
                    provider="hyperliquid",
                    target_id=target_id,
                    action_type="open_position",
                    summary=f"{position.get('symbol')} 新增{position.get('side')}仓位 ${notional:,.0f}",
                    payload={"position": position},
                )
                continue
            old_notional = abs(_float(old.get("notional")))
            if old_notional > 0:
                change_pct = abs(notional - old_notional) / old_notional * 100
                if change_pct >= threshold_pct:
                    self.store.add_whale_event(
                        provider="hyperliquid",
                        target_id=target_id,
                        action_type="position_change",
                        summary=f"{position.get('symbol')} 仓位变化 {change_pct:.1f}%",
                        payload={"old": old, "position": position, "change_pct": change_pct},
                    )
            distance = position.get("liquidation_distance_pct")
            old_distance = old.get("liquidation_distance_pct") if isinstance(old, dict) else None
            if distance is not None and float(distance) <= liquidation_distance and (old_distance is None or float(old_distance) > liquidation_distance):
                self.store.add_whale_event(
                    provider="hyperliquid",
                    target_id=target_id,
                    action_type="liquidation_risk",
                    summary=f"{position.get('symbol')} 强平距离 {float(distance):.2f}%",
                    payload={"position": position},
                )
        for key, position in old_positions.items():
            if key not in new_positions and abs(_float(position.get("notional"))) >= min_value:
                self.store.add_whale_event(
                    provider="hyperliquid",
                    target_id=target_id,
                    action_type="close_position",
                    summary=f"{position.get('symbol')} 已平{position.get('side')}仓位",
                    payload={"position": position},
                )


def _target_addresses(address_or_subject: str, config: dict[str, Any]) -> list[str]:
    addresses = extract_addresses(address_or_subject)
    for item in config.get("addresses", []):
        addresses.extend(extract_addresses(str(item)))
    return list(dict.fromkeys(addresses))


def _position_key(position: dict[str, Any]) -> str:
    return f"{position.get('symbol')}:{position.get('side')}"


def _fill_event_context(fill: dict[str, Any], current_positions: list[dict[str, Any]], old_positions: list[dict[str, Any]]) -> dict[str, Any]:
    position = _find_fill_position(fill, current_positions) or _find_fill_position(fill, old_positions)
    context: dict[str, Any] = {
        "direction_label": str(fill.get("direction_label") or _direction_label(fill)),
        "price_label": str(fill.get("price_label") or _price_label(fill)),
    }
    if position:
        context["position_leverage"] = position.get("leverage")
        context["position_margin_mode"] = position.get("margin_mode")
    return context


def _find_fill_position(fill: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    fill_coin = _coin_key(fill.get("coin") or fill.get("symbol"))
    if not fill_coin:
        return None
    for position in positions:
        if not isinstance(position, dict):
            continue
        if _coin_key(position.get("coin") or position.get("symbol")) == fill_coin:
            return position
    return None


def _coin_key(value: Any) -> str:
    text = str(value or "").upper()
    return text.split("-", 1)[0].strip()


def _direction_label(fill: dict[str, Any]) -> str:
    direction = str(fill.get("direction") or "").strip()
    normalized = direction.lower().replace("_", " ")
    if "open long" in normalized:
        return "买入开多"
    if "close long" in normalized:
        return "卖出平多"
    if "open short" in normalized:
        return "卖出开空"
    if "close short" in normalized:
        return "买入平空"
    return direction or str(fill.get("side") or "--")


def _price_label(fill: dict[str, Any]) -> str:
    normalized = str(fill.get("direction") or "").strip().lower().replace("_", " ")
    if "close" in normalized:
        return "平仓价格"
    if "open" in normalized:
        return "开仓价格"
    return "成交价格"


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _int(value: Any, fallback: int = 0) -> int:
    try:
        if value in (None, ""):
            return fallback
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fill_cursor_key(target_id: str, address: str) -> str:
    return f"whale:hyperliquid:fills:{target_id}:{address.lower()}"


def _extended_cursor_key(target_id: str, address: str) -> str:
    return f"whale:hyperliquid:extended:{target_id}:{address.lower()}"


def _preserve_extended_snapshot_fields(snapshot: Any, old_snapshot: dict[str, Any]) -> None:
    for field in ("fills", "historical_orders", "funding", "ledger_updates", "portfolio"):
        value = old_snapshot.get(field)
        if isinstance(value, list):
            setattr(snapshot, field, value)
    old_account = old_snapshot.get("account_summary") if isinstance(old_snapshot.get("account_summary"), dict) else {}
    if old_account and "fees" in old_account:
        snapshot.account_summary["fees"] = old_account["fees"]


def _load_cursor(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _initial_fill_start_ms(config: dict[str, Any], now_ms: int) -> int | None:
    mode = str(config.get("initial_fill_sync_mode") or "cursor_only")
    if mode == "lookback_3h":
        return now_ms - 3 * 60 * 60 * 1000
    if mode == "today":
        now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return int(today.timestamp() * 1000)
    return None


def _fill_identity(fill: dict[str, Any]) -> str:
    parts = [
        fill.get("hash"),
        fill.get("trade_id"),
        fill.get("order_id"),
        fill.get("timestamp"),
        fill.get("coin"),
        fill.get("side_code"),
        fill.get("price"),
        fill.get("size"),
    ]
    return ":".join(str(part) for part in parts if part not in (None, ""))


def _fill_time_iso(fill: dict[str, Any]) -> str:
    timestamp = _int(fill.get("timestamp"))
    if timestamp > 0:
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _is_large_fill(fill: dict[str, Any], config: dict[str, Any]) -> bool:
    notional_threshold = _float(config.get("trade_min_notional_usd"), 100000)
    if _float(fill.get("notional")) >= notional_threshold:
        return True
    raw_thresholds = config.get("trade_coin_thresholds")
    thresholds = raw_thresholds if isinstance(raw_thresholds, dict) else {"ETH": 100, "BTC": 5, "SOL": 10000}
    coin = str(fill.get("coin") or "").upper()
    threshold = _float(thresholds.get(coin), 0)
    return threshold > 0 and abs(_float(fill.get("size"))) >= threshold
