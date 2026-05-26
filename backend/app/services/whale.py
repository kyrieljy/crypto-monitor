from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
MASKED_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{3,12}\.{2,}[a-fA-F0-9]{3,12}")


class WhaleProviderError(Exception):
    pass


@dataclass
class WhaleSnapshot:
    positions: list[dict[str, Any]] = field(default_factory=list)
    holdings: list[dict[str, Any]] = field(default_factory=list)
    defi_positions: list[dict[str, Any]] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    historical_orders: list[dict[str, Any]] = field(default_factory=list)
    funding: list[dict[str, Any]] = field(default_factory=list)
    ledger_updates: list[dict[str, Any]] = field(default_factory=list)
    portfolio: list[dict[str, Any]] = field(default_factory=list)
    account_summary: dict[str, Any] = field(default_factory=dict)
    source_status: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": self.positions,
            "holdings": self.holdings,
            "defi_positions": self.defi_positions,
            "open_orders": self.open_orders,
            "fills": self.fills,
            "historical_orders": self.historical_orders,
            "funding": self.funding,
            "ledger_updates": self.ledger_updates,
            "portfolio": self.portfolio,
            "account_summary": self.account_summary,
            "source_status": self.source_status,
            "raw": self.raw,
        }


def extract_addresses(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0).lower() for match in ADDRESS_RE.finditer(text or "")))


def is_masked_address(text: str) -> bool:
    return bool(MASKED_ADDRESS_RE.search(text or ""))


def resolve_address_candidates(query: str, existing_targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = (query or "").strip()
    candidates: list[dict[str, Any]] = []
    for address in extract_addresses(text):
        candidates.append(
            {
                "address": address,
                "label": _label_from_query(text, address),
                "source": _source_from_query(text),
                "chain": "evm",
                "url": text if text.startswith(("http://", "https://")) else None,
                "confidence": 0.95,
            }
        )

    lowered = text.lower()
    if not candidates:
        for target in existing_targets:
            label = str(target.get("label") or "")
            if not label or lowered not in label.lower():
                continue
            config = target.get("config") if isinstance(target.get("config"), dict) else {}
            addresses = [str(item).lower() for item in config.get("addresses", []) if ADDRESS_RE.fullmatch(str(item))]
            direct = str(target.get("address_or_subject") or "").lower()
            if ADDRESS_RE.fullmatch(direct):
                addresses.insert(0, direct)
            for address in dict.fromkeys(addresses):
                candidates.append(
                    {
                        "address": address,
                        "label": label,
                        "source": "local",
                        "chain": "evm",
                        "url": config.get("source_url") if isinstance(config.get("source_url"), str) else None,
                        "confidence": 0.75,
                        "target_id": target.get("id"),
                    }
                )

    return candidates


def _label_from_query(query: str, address: str) -> str:
    if query.startswith(("http://", "https://")):
        host = re.sub(r"^https?://", "", query).split("/", 1)[0]
        return host or "关注地址"
    text = query.replace(address, "").strip(" -_/|")
    return text[:32] or "关注地址"


def _source_from_query(query: str) -> str:
    lowered = query.lower()
    if "hyperliquid" in lowered or "hyperdash" in lowered:
        return "hyperliquid_link"
    if "etherscan" in lowered:
        return "etherscan_link"
    if "debank" in lowered:
        return "debank_link"
    if "arkm" in lowered or "arkham" in lowered:
        return "arkham_link"
    if query.startswith(("http://", "https://")):
        return "web_link"
    return "manual"


class HyperliquidProvider:
    source_name = "hyperliquid"
    label = "Hyperliquid"
    max_retries = 3

    def __init__(self, base_url: str = "https://api.hyperliquid.xyz", timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch(self, address: str, *, include_extended: bool = True) -> WhaleSnapshot:
        state = self._post({"type": "clearinghouseState", "user": address})
        orders = self._post_optional({"type": "frontendOpenOrders", "user": address})
        if orders is None:
            orders = self._post({"type": "openOrders", "user": address})
        market_mids: dict[str, float] = {}
        market_context: dict[str, dict[str, Any]] = {}
        market_warning = ""
        try:
            market_mids = self.parse_market_mids(self._post({"type": "allMids"}))
        except WhaleProviderError as exc:
            market_warning = str(exc)
        try:
            market_context = self.parse_market_context(self._post({"type": "metaAndAssetCtxs"}))
        except WhaleProviderError as exc:
            market_warning = "; ".join([item for item in (market_warning, str(exc)) if item])

        recent_fills = None
        historical_orders = None
        funding = None
        ledger_updates = None
        portfolio = None
        fees = None
        if include_extended:
            now_ms = _now_ms()
            start_ms = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
            recent_fills = self._post_optional({"type": "userFills", "user": address, "aggregateByTime": True})
            historical_orders = self._post_optional({"type": "historicalOrders", "user": address})
            funding = self._post_optional({"type": "userFunding", "user": address, "startTime": start_ms, "endTime": now_ms})
            ledger_updates = self._post_optional({"type": "userNonFundingLedgerUpdates", "user": address, "startTime": start_ms, "endTime": now_ms})
            portfolio = self._post_optional({"type": "portfolio", "user": address})
            fees = self._post_optional({"type": "userFees", "user": address})

        positions = self.parse_positions(state, market_mids, market_context)
        source_status = {
            "hyperliquid": {
                "ok": True,
                "market_data": "metaAndAssetCtxs" if market_context else "allMids" if market_mids else "position",
            }
        }
        if market_warning:
            source_status["hyperliquid"]["market_warning"] = market_warning
        account_summary = self.parse_account_summary(state)
        if fees is not None:
            account_summary["fees"] = fees
        snapshot = WhaleSnapshot(
            positions=positions,
            open_orders=self.parse_open_orders(orders),
            fills=self.parse_fills(recent_fills),
            historical_orders=self.parse_historical_orders(historical_orders),
            funding=self.parse_funding(funding),
            ledger_updates=self.parse_ledger_updates(ledger_updates),
            portfolio=self.parse_portfolio(portfolio),
            account_summary=account_summary,
            source_status=source_status,
            raw={
                "hyperliquid": {
                    "clearinghouseState": state,
                    "openOrders": orders,
                    "position_mids": _position_mids(positions, market_mids),
                    "market_context": market_context,
                    "fees": fees,
                }
            },
        )
        return snapshot

    def fetch_fills_by_time(self, address: str, start_time_ms: int, end_time_ms: int | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "type": "userFillsByTime",
            "user": address,
            "startTime": max(0, int(start_time_ms)),
            "aggregateByTime": True,
        }
        if end_time_ms is not None:
            payload["endTime"] = int(end_time_ms)
        return self.parse_fills(self._post(payload))

    def fetch_recent_fills(self, address: str) -> list[dict[str, Any]]:
        return self.parse_fills(self._post({"type": "userFills", "user": address, "aggregateByTime": True}))

    def _post(self, payload: dict[str, Any]) -> Any:
        errors: list[str] = []
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/info",
                    json=payload,
                    timeout=self.timeout_seconds,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "CryptoMonitor/0.1",
                        "Connection": "close",
                    },
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                errors.append(str(exc))
                if attempt >= self.max_retries or not _is_retryable_request_error(exc):
                    break
                time.sleep(0.4 * attempt)
        raise WhaleProviderError(f"Hyperliquid request failed after {len(errors)} attempt(s): {' | '.join(errors[-2:])}")

    def _post_optional(self, payload: dict[str, Any]) -> Any | None:
        try:
            return self._post(payload)
        except WhaleProviderError:
            return None

    @staticmethod
    def parse_positions(payload: dict[str, Any], market_mids: dict[str, Any] | None = None, market_context: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = payload.get("assetPositions", []) if isinstance(payload, dict) else []
        positions: list[dict[str, Any]] = []
        for row in rows:
            position = row.get("position", row) if isinstance(row, dict) else {}
            if not isinstance(position, dict):
                continue
            size = _float(position.get("szi"))
            if size == 0:
                continue
            leverage = position.get("leverage") if isinstance(position.get("leverage"), dict) else {}
            cum_funding = position.get("cumFunding") if isinstance(position.get("cumFunding"), dict) else {}
            coin = str(position.get("coin") or position.get("symbol") or "UNKNOWN")
            mark_price = _optional_float(position.get("markPx") or position.get("mark_price"))
            if mark_price is None:
                mark_price = _mid_for_coin(market_mids or {}, coin)
            context = _context_for_coin(market_context or {}, coin)
            if mark_price is None and context:
                mark_price = _optional_float(context.get("mark_price") or context.get("mid_price"))
            liquidation_price = _optional_float(position.get("liquidationPx") or position.get("liquidation_price"))
            notional = _float(position.get("positionValue"))
            if notional == 0 and mark_price is not None:
                notional = abs(size) * mark_price
            positions.append(
                {
                    "symbol": f"{coin}-USDC" if "-" not in coin else coin,
                    "coin": coin,
                    "side": "做多" if size > 0 else "做空",
                    "size": abs(size),
                    "signed_size": size,
                    "margin_mode": "逐仓" if leverage.get("type") == "isolated" else "全仓",
                    "leverage": int(_float(leverage.get("value"), 0)) or _float(position.get("maxLeverage"), 0),
                    "entry_price": _optional_float(position.get("entryPx")),
                    "mark_price": mark_price,
                    "liquidation_price": liquidation_price,
                    "margin": _float(position.get("marginUsed")),
                    "notional": notional,
                    "pnl": _float(position.get("unrealizedPnl")),
                    "pnl_percent": _float(position.get("returnOnEquity")) * 100,
                    "funding": _float(cum_funding.get("sinceOpen") or cum_funding.get("allTime")),
                    "funding_rate": _optional_float(context.get("funding")) if context else None,
                    "open_interest": _optional_float(context.get("open_interest")) if context else None,
                    "liquidation_distance_pct": _distance_pct(mark_price, liquidation_price),
                }
            )
        return positions

    @staticmethod
    def parse_market_mids(payload: Any) -> dict[str, float]:
        if not isinstance(payload, dict):
            return {}
        mids: dict[str, float] = {}
        for coin, price in payload.items():
            value = _optional_float(price)
            if value is not None:
                mids[str(coin).upper()] = value
        return mids

    @staticmethod
    def parse_market_context(payload: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, list) or len(payload) < 2:
            return {}
        meta = payload[0] if isinstance(payload[0], dict) else {}
        contexts = payload[1] if isinstance(payload[1], list) else []
        universe = meta.get("universe") if isinstance(meta.get("universe"), list) else []
        result: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(universe):
            if not isinstance(item, dict):
                continue
            coin = str(item.get("name") or "").upper()
            if not coin:
                continue
            ctx = contexts[index] if index < len(contexts) and isinstance(contexts[index], dict) else {}
            result[coin] = {
                "coin": coin,
                "mark_price": _optional_float(ctx.get("markPx")),
                "mid_price": _optional_float(ctx.get("midPx")),
                "oracle_price": _optional_float(ctx.get("oraclePx")),
                "funding": _optional_float(ctx.get("funding")),
                "open_interest": _optional_float(ctx.get("openInterest")),
                "day_notional_volume": _optional_float(ctx.get("dayNtlVlm")),
                "max_leverage": _optional_float(item.get("maxLeverage")),
            }
        return result

    @staticmethod
    def parse_open_orders(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        orders: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            coin = str(row.get("coin") or "UNKNOWN")
            orders.append(
                {
                    "symbol": f"{coin}-USDC" if "-" not in coin else coin,
                    "side": "买入" if row.get("side") == "B" else "卖出" if row.get("side") == "A" else str(row.get("side") or "--"),
                    "price": _float(row.get("limitPx") or row.get("px")),
                    "size": _float(row.get("sz") or row.get("origSz")),
                    "notional": _float(row.get("sz") or row.get("origSz")) * _float(row.get("limitPx") or row.get("px")),
                    "order_type": str(row.get("orderType") or row.get("tif") or ""),
                    "order_id": row.get("oid"),
                    "timestamp": row.get("timestamp") or row.get("time"),
                    "reduce_only": bool(row.get("reduceOnly")),
                }
            )
        return orders

    @staticmethod
    def parse_fills(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        fills: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            coin = str(row.get("coin") or "UNKNOWN")
            price = _float(row.get("px") or row.get("price"))
            size = _float(row.get("sz") or row.get("size"))
            side = str(row.get("side") or "")
            direction = str(row.get("dir") or "")
            fills.append(
                {
                    "symbol": f"{coin}-USDC" if "-" not in coin else coin,
                    "coin": coin,
                    "side": "买入" if side == "B" else "卖出" if side == "A" else side or "--",
                    "side_code": side,
                    "direction": direction,
                    "direction_label": _fill_direction_label(side, direction),
                    "price_label": _fill_price_label(direction),
                    "price": price,
                    "size": size,
                    "notional": abs(price * size),
                    "fee": _optional_float(row.get("fee")),
                    "fee_token": row.get("feeToken"),
                    "closed_pnl": _optional_float(row.get("closedPnl")),
                    "order_id": row.get("oid"),
                    "trade_id": row.get("tid"),
                    "hash": row.get("hash"),
                    "crossed": bool(row.get("crossed")),
                    "timestamp": row.get("time") or row.get("timestamp"),
                    "raw": row,
                }
            )
        return sorted(fills, key=lambda item: int(_float(item.get("timestamp"))), reverse=True)

    @staticmethod
    def parse_historical_orders(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        orders: list[dict[str, Any]] = []
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            order = row.get("order") if isinstance(row.get("order"), dict) else row
            coin = str(order.get("coin") or "UNKNOWN")
            side = str(order.get("side") or "")
            price = _float(order.get("limitPx") or order.get("px"))
            size = _float(order.get("sz") or order.get("origSz"))
            orders.append(
                {
                    "symbol": f"{coin}-USDC" if "-" not in coin else coin,
                    "side": "买入" if side == "B" else "卖出" if side == "A" else side or "--",
                    "price": price,
                    "size": size,
                    "notional": abs(price * size),
                    "status": str(row.get("status") or order.get("status") or "--"),
                    "order_type": str(order.get("orderType") or order.get("tif") or ""),
                    "order_id": order.get("oid"),
                    "timestamp": row.get("statusTimestamp") or order.get("timestamp") or order.get("time"),
                    "raw": row,
                }
            )
        return sorted(orders, key=lambda item: int(_float(item.get("timestamp"))), reverse=True)

    @staticmethod
    def parse_funding(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        result: list[dict[str, Any]] = []
        for row in rows[-100:]:
            if not isinstance(row, dict):
                continue
            delta = row.get("delta") if isinstance(row.get("delta"), dict) else row
            result.append(
                {
                    "coin": str(delta.get("coin") or row.get("coin") or ""),
                    "amount": _optional_float(delta.get("usdc") or row.get("usdc") or row.get("amount")),
                    "funding_rate": _optional_float(delta.get("fundingRate") or row.get("fundingRate")),
                    "position_size": _optional_float(delta.get("szi") or row.get("szi")),
                    "timestamp": row.get("time") or row.get("timestamp"),
                    "raw": row,
                }
            )
        return sorted(result, key=lambda item: int(_float(item.get("timestamp"))), reverse=True)

    @staticmethod
    def parse_ledger_updates(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        result: list[dict[str, Any]] = []
        for row in rows[-100:]:
            if not isinstance(row, dict):
                continue
            delta = row.get("delta") if isinstance(row.get("delta"), dict) else row
            result.append(
                {
                    "type": str(delta.get("type") or row.get("type") or "ledger"),
                    "amount": _optional_float(delta.get("usdc") or delta.get("amount") or row.get("amount")),
                    "hash": row.get("hash"),
                    "timestamp": row.get("time") or row.get("timestamp"),
                    "raw": row,
                }
            )
        return sorted(result, key=lambda item: int(_float(item.get("timestamp"))), reverse=True)

    @staticmethod
    def parse_portfolio(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def parse_account_summary(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        margin = payload.get("marginSummary") if isinstance(payload.get("marginSummary"), dict) else {}
        cross = payload.get("crossMarginSummary") if isinstance(payload.get("crossMarginSummary"), dict) else {}
        return {
            "account_value": _float(margin.get("accountValue") or cross.get("accountValue")),
            "total_margin_used": _float(margin.get("totalMarginUsed") or cross.get("totalMarginUsed")),
            "total_notional": _float(margin.get("totalNtlPos") or cross.get("totalNtlPos")),
            "withdrawable": _optional_float(payload.get("withdrawable")),
            "source": "hyperliquid",
        }


class DeBankProvider:
    source_name = "debank"
    label = "DeBank"

    def __init__(self, base_url: str = "https://pro-openapi.debank.com", access_key: str = "", timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_key = access_key
        self.timeout_seconds = timeout_seconds

    def fetch(self, address: str) -> WhaleSnapshot:
        if not self.access_key:
            return WhaleSnapshot(source_status={"debank": {"ok": False, "skipped": True, "message": "未配置 DeBank AccessKey"}})
        total = self._get("/v1/user/total_balance", {"id": address})
        tokens = self._get("/v1/user/all_token_list", {"id": address, "is_all": "false"})
        protocols = self._get("/v1/user/all_simple_protocol_list", {"id": address})
        return WhaleSnapshot(
            holdings=self.parse_holdings(tokens),
            defi_positions=self.parse_protocols(protocols),
            account_summary={"total_balance": _float(total.get("total_usd_value") if isinstance(total, dict) else total), "source": "debank"},
            source_status={"debank": {"ok": True}},
            raw={"debank": {"total_balance": total, "token_count": len(tokens) if isinstance(tokens, list) else 0}},
        )

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            response = requests.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"AccessKey": self.access_key, "accept": "application/json"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise WhaleProviderError(f"DeBank request failed: {exc}") from exc

    @staticmethod
    def parse_holdings(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        holdings = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = _float(row.get("amount")) * _float(row.get("price"))
            if value <= 0:
                continue
            holdings.append(
                {
                    "symbol": str(row.get("symbol") or row.get("optimized_symbol") or "UNKNOWN"),
                    "name": str(row.get("name") or ""),
                    "chain": str(row.get("chain") or ""),
                    "amount": _float(row.get("amount")),
                    "price": _float(row.get("price")),
                    "value": value,
                    "logo_url": row.get("logo_url"),
                }
            )
        return sorted(holdings, key=lambda item: float(item["value"]), reverse=True)[:30]

    @staticmethod
    def parse_protocols(payload: Any) -> list[dict[str, Any]]:
        rows = payload if isinstance(payload, list) else []
        protocols = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = _float(row.get("net_usd_value"))
            portfolio_items = row.get("portfolio_item_list") if isinstance(row.get("portfolio_item_list"), list) else []
            protocols.append(
                {
                    "name": str(row.get("name") or row.get("id") or "Protocol"),
                    "chain": str(row.get("chain") or ""),
                    "value": value,
                    "item_count": len(portfolio_items),
                    "logo_url": row.get("logo_url"),
                }
            )
        return sorted(protocols, key=lambda item: float(item["value"]), reverse=True)[:30]


def merge_snapshots(snapshots: list[WhaleSnapshot]) -> dict[str, Any]:
    merged = WhaleSnapshot()
    for snapshot in snapshots:
        merged.positions.extend(snapshot.positions)
        merged.holdings.extend(snapshot.holdings)
        merged.defi_positions.extend(snapshot.defi_positions)
        merged.open_orders.extend(snapshot.open_orders)
        merged.fills.extend(snapshot.fills)
        merged.historical_orders.extend(snapshot.historical_orders)
        merged.funding.extend(snapshot.funding)
        merged.ledger_updates.extend(snapshot.ledger_updates)
        merged.portfolio.extend(snapshot.portfolio)
        merged.account_summary.update({key: value for key, value in snapshot.account_summary.items() if value not in ("", None)})
        merged.source_status.update(snapshot.source_status)
        merged.raw.update(snapshot.raw)
    if merged.positions:
        merged.account_summary["contract_notional"] = sum(_float(item.get("notional")) for item in merged.positions)
        merged.account_summary["contract_pnl"] = sum(_float(item.get("pnl")) for item in merged.positions)
    if merged.holdings:
        merged.account_summary["spot_value"] = sum(_float(item.get("value")) for item in merged.holdings)
    if merged.defi_positions:
        merged.account_summary["defi_value"] = sum(_float(item.get("value")) for item in merged.defi_positions)
    return merged.to_dict()


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mid_for_coin(market_mids: dict[str, Any], coin: str) -> float | None:
    keys = [coin, coin.upper(), coin.split("-", 1)[0], coin.split("-", 1)[0].upper()]
    for key in keys:
        value = _optional_float(market_mids.get(key))
        if value is not None:
            return value
    return None


def _context_for_coin(market_context: dict[str, dict[str, Any]], coin: str) -> dict[str, Any]:
    keys = [coin, coin.upper(), coin.split("-", 1)[0], coin.split("-", 1)[0].upper()]
    for key in keys:
        value = market_context.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _position_mids(positions: list[dict[str, Any]], market_mids: dict[str, Any]) -> dict[str, float]:
    mids: dict[str, float] = {}
    for position in positions:
        coin = str(position.get("coin") or "")
        value = _mid_for_coin(market_mids, coin)
        if value is not None:
            mids[coin] = value
    return mids


def _distance_pct(mark_price: float | None, liquidation_price: float | None) -> float | None:
    if mark_price is None or liquidation_price is None or mark_price <= 0 or liquidation_price <= 0:
        return None
    return abs(mark_price - liquidation_price) / mark_price * 100


def _fill_direction_label(side_code: str, direction: str) -> str:
    normalized = str(direction or "").strip().lower().replace("_", " ")
    if "liquidated" in normalized:
        margin = "全仓" if "cross" in normalized else "逐仓" if "isolated" in normalized else ""
        side = "多单" if "long" in normalized else "空单" if "short" in normalized else ""
        return f"强平{margin}{side}" if margin or side else "强平"
    if "open long" in normalized:
        return "买入开多"
    if "close long" in normalized:
        return "卖出平多"
    if "open short" in normalized:
        return "卖出开空"
    if "close short" in normalized:
        return "买入平空"
    if direction:
        return str(direction)
    return "买入" if side_code == "B" else "卖出" if side_code == "A" else "--"


def _fill_price_label(direction: str) -> str:
    normalized = str(direction or "").strip().lower().replace("_", " ")
    if "liquidated" in normalized:
        return "强平价格"
    if "close" in normalized:
        return "平仓价格"
    if "open" in normalized:
        return "开仓价格"
    return "成交价格"


def _is_retryable_request_error(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, requests.exceptions.SSLError)):
        return True
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code in {429, 500, 502, 503, 504}


def _now_ms() -> int:
    return int(time.time() * 1000)
