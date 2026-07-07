from __future__ import annotations

import hashlib
import re
import time
from email.utils import parsedate_to_datetime
from html import unescape
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET

import requests


ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{40}")
MASKED_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{3,12}\.{2,}[a-fA-F0-9]{3,12}")
BTC_ADDRESS_RE = re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{11,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b", re.IGNORECASE)
BTC_TXID_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
DEFAULT_IBIT_NEWS_KEYWORDS = [
    "IBIT",
    "BlackRock",
    "iShares Bitcoin Trust",
    "贝莱德",
    "Coinbase",
    "OnchainLens",
    "Lookonchain",
    "Ai 姨",
    "链上",
    "地址",
    "txid",
]


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


def extract_btc_addresses(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in BTC_ADDRESS_RE.finditer(text or "")))


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


class BlackRockFreeProvider:
    source_name = "blackrock_free"
    label = "IBIT Free Monitor"

    def __init__(
        self,
        *,
        ishares_url: str = "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf",
        farside_url: str = "https://farside.co.uk/btc/",
        blockstream_base_url: str = "https://blockstream.info/api",
        timeout_seconds: int = 20,
    ) -> None:
        self.ishares_url = ishares_url
        self.farside_url = farside_url
        self.blockstream_base_url = blockstream_base_url.rstrip("/")
        self.blockstream_fallback_base_urls = [
            url for url in ("https://mempool.space/api",)
            if url.rstrip("/") != self.blockstream_base_url
        ]
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        *,
        btc_addresses: list[str],
        suspected_btc_addresses: list[str] | None = None,
        transfer_min_btc: float = 1000.0,
        transfer_lookback_hours: int = 24,
        news_feed_urls: list[str] | None = None,
        news_keywords: list[str] | None = None,
        news_lookback_hours: int = 72,
        news_max_items: int = 60,
        news_enabled: bool = False,
    ) -> WhaleSnapshot:
        account_summary: dict[str, Any] = {
            "source": "blackrock_free",
            "monitoring_mode": "official_flow_and_confirmed_btc_cluster",
        }
        source_status: dict[str, Any] = {}
        raw: dict[str, Any] = {}

        try:
            official = self.fetch_ishares_snapshot()
            raw["ishares"] = official
            account_summary.update(
                {
                    "blackrock_official_date": official.get("as_of"),
                    "blackrock_official_net_assets": official.get("net_assets"),
                    "blackrock_official_estimated_btc": official.get("estimated_btc_holdings"),
                    "blackrock_official_benchmark_price": official.get("benchmark_price"),
                    "blackrock_official_shares_outstanding": official.get("shares_outstanding"),
                }
            )
            source_status["ishares"] = {"ok": True, "date": official.get("as_of")}
        except WhaleProviderError as exc:
            source_status["ishares"] = {"ok": False, "message": str(exc)}

        try:
            flow = self.fetch_farside_flow()
            raw["farside"] = flow
            flow_usd = _optional_float(flow.get("ibit_flow_usd_m"))
            account_summary.update(
                {
                    "blackrock_last_flow_date": flow.get("date"),
                    "blackrock_last_flow_usd": flow_usd * 1_000_000 if flow_usd is not None else None,
                    "blackrock_last_flow_usd_m": flow_usd,
                }
            )
            source_status["farside"] = {"ok": True, "date": flow.get("date")}
        except WhaleProviderError as exc:
            source_status["farside"] = {"ok": False, "message": str(exc)}

        transfers: list[dict[str, Any]] = []
        address_activity: dict[str, list[dict[str, Any]]] = {}
        if btc_addresses:
            try:
                transfers = self.fetch_confirmed_btc_transfers(
                    btc_addresses,
                    transfer_min_btc=transfer_min_btc,
                    lookback_hours=transfer_lookback_hours,
                )
                address_activity = self.fetch_btc_address_activity(btc_addresses, lookback_hours=transfer_lookback_hours)
                raw["btc_cluster"] = {"addresses": btc_addresses, "transfers": transfers[:20], "address_activity": address_activity}
                activity_count = sum(len(items) for items in address_activity.values())
                source_status["btc_cluster"] = {"ok": True, "addresses": len(btc_addresses), "transfers": len(transfers), "operations": activity_count}
                latest = transfers[0] if transfers else None
                account_summary.update(
                    {
                        "blackrock_btc_cluster_address_count": len(btc_addresses),
                        "blackrock_btc_cluster_transfer_count": len(transfers),
                        "blackrock_btc_cluster_operation_count": activity_count,
                        "blackrock_btc_cluster_last_outflow_btc": latest.get("amount_btc") if isinstance(latest, dict) else None,
                    }
                )
            except WhaleProviderError as exc:
                source_status["btc_cluster"] = {"ok": False, "message": str(exc), "addresses": len(btc_addresses)}
        else:
            source_status["btc_cluster"] = {"ok": False, "skipped": True, "message": "未配置已确认 BTC 地址簇"}

        if news_enabled and news_feed_urls:
            try:
                news_signals = self.fetch_news_signals(
                    news_feed_urls,
                    keywords=news_keywords or DEFAULT_IBIT_NEWS_KEYWORDS,
                    lookback_hours=news_lookback_hours,
                    max_items=news_max_items,
                    expected_flow_usd=_optional_float(account_summary.get("blackrock_last_flow_usd")),
                )
                suspected_pool = [address for address in dict.fromkeys(suspected_btc_addresses or []) if address not in set(btc_addresses)]
                suspected_activity = self.fetch_btc_address_activity(suspected_pool[:100], lookback_hours=news_lookback_hours) if suspected_pool else {}
                matched_activity = {**address_activity, **suspected_activity}
                suspected_addresses = self.build_suspected_addresses(news_signals, candidate_activity=matched_activity)
                raw["news_signals"] = {
                    "feed_urls": news_feed_urls,
                    "signals": news_signals[: max(1, news_max_items)],
                    "suspected_addresses": suspected_addresses[:200],
                    "suspected_address_pool": suspected_pool,
                    "suspected_address_activity": suspected_activity,
                    "confirmed_address_activity": address_activity,
                }
                account_summary["ibit_news_candidate_count"] = len(news_signals)
                account_summary["ibit_suspected_address_count"] = len(suspected_addresses)
                source_status["news_signals"] = {"ok": True, "feeds": len(news_feed_urls), "signals": len(news_signals)}
            except WhaleProviderError as exc:
                source_status["news_signals"] = {"ok": False, "message": str(exc), "feeds": len(news_feed_urls)}
        elif news_enabled:
            source_status["news_signals"] = {"ok": False, "skipped": True, "message": "未配置 IBIT 新闻/RSS 源"}

        holding_value = _optional_float(account_summary.get("blackrock_official_net_assets"))
        holding_amount = _optional_float(account_summary.get("blackrock_official_estimated_btc"))
        holding_price = _optional_float(account_summary.get("blackrock_official_benchmark_price"))
        holdings = []
        if holding_value is not None or holding_amount is not None:
            holdings.append(
                {
                    "symbol": "IBIT-BTC",
                    "name": "iShares Bitcoin Trust ETF estimated BTC holdings",
                    "chain": "official",
                    "amount": holding_amount,
                    "price": holding_price,
                    "value": holding_value,
                    "logo_url": None,
                    "estimated": True,
                }
            )

        return WhaleSnapshot(
            holdings=holdings,
            account_summary=account_summary,
            source_status=source_status,
            raw=raw,
        )

    def fetch_ishares_snapshot(self) -> dict[str, Any]:
        text = self._get_text(self.ishares_url)
        parsed = self.parse_ishares_page(text)
        if not parsed:
            raise WhaleProviderError("iShares IBIT 页面未解析到关键数据")
        parsed["source_url"] = self.ishares_url
        return parsed

    def fetch_farside_flow(self) -> dict[str, Any]:
        text = self._get_text(self.farside_url)
        parsed = self.parse_farside_flows(text)
        if not parsed:
            raise WhaleProviderError("Farside BTC ETF Flow 页面未解析到 IBIT 数据")
        parsed["source_url"] = self.farside_url
        return parsed

    def fetch_confirmed_btc_transfers(self, addresses: list[str], *, transfer_min_btc: float, lookback_hours: int) -> list[dict[str, Any]]:
        watched = set(dict.fromkeys(addresses))
        txs: dict[str, dict[str, Any]] = {}
        for address in watched:
            for tx in self.fetch_btc_address_txs(address, lookback_hours=lookback_hours):
                if isinstance(tx, dict) and tx.get("txid"):
                    txs[str(tx["txid"])] = tx
        start_ms = int((datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).timestamp() * 1000)
        transfers = []
        for tx in txs.values():
            transfer = self.parse_btc_cluster_transfer(tx, watched)
            if not transfer:
                continue
            if int(transfer.get("timestamp_ms") or 0) < start_ms:
                continue
            if _float(transfer.get("amount_btc")) < transfer_min_btc:
                continue
            transfers.append(transfer)
        return sorted(transfers, key=lambda item: int(item.get("timestamp_ms") or 0), reverse=True)

    def fetch_btc_address_activity(self, addresses: list[str], *, lookback_hours: int) -> dict[str, list[dict[str, Any]]]:
        start_ms = int((datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).timestamp() * 1000)
        activity: dict[str, list[dict[str, Any]]] = {}
        for address in dict.fromkeys(addresses):
            operations: list[dict[str, Any]] = []
            for tx in self.fetch_btc_address_txs(address, lookback_hours=lookback_hours):
                if not isinstance(tx, dict):
                    continue
                operation = self.parse_btc_address_operation(tx, address)
                if not operation:
                    continue
                if int(operation.get("timestamp_ms") or 0) < start_ms:
                    continue
                operations.append(operation)
            activity[address] = sorted(operations, key=lambda item: int(item.get("timestamp_ms") or 0), reverse=True)[:50]
        return activity

    def fetch_btc_address_txs(self, address: str, *, lookback_hours: int, max_pages: int = 12) -> list[dict[str, Any]]:
        start_ms = int((datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))).timestamp() * 1000)
        quoted = quote(address, safe="")
        txs: list[dict[str, Any]] = []
        seen: set[str] = set()
        path = f"/address/{quoted}/txs"
        for _ in range(max(1, max_pages)):
            page = self._get_json(path)
            if not isinstance(page, list) or not page:
                break
            stop = False
            last_txid = ""
            for tx in page:
                if not isinstance(tx, dict):
                    continue
                txid = str(tx.get("txid") or "")
                if not txid or txid in seen:
                    continue
                seen.add(txid)
                last_txid = txid
                status = tx.get("status") if isinstance(tx.get("status"), dict) else {}
                block_time = _int(status.get("block_time"))
                timestamp_ms = block_time * 1000 if block_time > 0 else _now_ms()
                if timestamp_ms < start_ms:
                    stop = True
                    continue
                txs.append(tx)
            if stop or not last_txid:
                break
            path = f"/address/{quoted}/txs/chain/{quote(last_txid, safe='')}"
        return txs

    def fetch_news_signals(
        self,
        feed_urls: list[str],
        *,
        keywords: list[str],
        lookback_hours: int,
        max_items: int,
        expected_flow_usd: float | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
        signals: list[dict[str, Any]] = []
        errors: list[str] = []
        for feed_url in feed_urls:
            try:
                text = self._get_text(feed_url)
                for signal in self.parse_news_feed(text, feed_url=feed_url, keywords=keywords)[: max(1, max_items)]:
                    published_at = _parse_feed_datetime(str(signal.get("published_at") or ""))
                    if published_at < cutoff:
                        continue
                    enriched = self._enrich_news_signal_with_txs(signal)
                    score, reasons = _score_ibit_news_signal(enriched, expected_flow_usd)
                    enriched["confidence"] = score
                    enriched["reasons"] = reasons
                    if score < 0.4 and not enriched.get("txids") and not enriched.get("candidate_addresses"):
                        continue
                    signals.append(enriched)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{feed_url}: {exc}")
        if not signals and errors and len(errors) == len(feed_urls):
            raise WhaleProviderError("; ".join(errors)[:800])
        return sorted(signals, key=lambda item: (float(item.get("confidence") or 0), str(item.get("published_at") or "")), reverse=True)

    def _enrich_news_signal_with_txs(self, signal: dict[str, Any]) -> dict[str, Any]:
        txids = [str(txid) for txid in signal.get("txids", []) if BTC_TXID_RE.fullmatch(str(txid))]
        if not txids:
            return signal
        enriched = dict(signal)
        tx_candidates: list[dict[str, Any]] = []
        candidate_addresses = list(signal.get("candidate_addresses") or [])
        for txid in txids[:5]:
            try:
                tx = self._get_json(f"/tx/{quote(txid, safe='')}")
            except WhaleProviderError as exc:
                tx_candidates.append({"txid": txid, "error": str(exc), "source_url": f"https://blockstream.info/tx/{txid}"})
                continue
            parsed = self.parse_btc_tx_address_candidates(tx)
            if parsed:
                tx_candidates.append(parsed)
                candidate_addresses.extend(parsed.get("input_addresses") or [])
        enriched["tx_candidates"] = tx_candidates
        enriched["candidate_addresses"] = list(dict.fromkeys(str(item) for item in candidate_addresses if item))
        return enriched

    @staticmethod
    def build_suspected_addresses(signals: list[dict[str, Any]], candidate_activity: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            tx_candidates = signal.get("tx_candidates") if isinstance(signal.get("tx_candidates"), list) else []
            linked_by_tx: dict[str, list[str]] = {}
            for tx in tx_candidates:
                if not isinstance(tx, dict):
                    continue
                txid = str(tx.get("txid") or "")
                for address in tx.get("input_addresses", []) if isinstance(tx.get("input_addresses"), list) else []:
                    linked_by_tx.setdefault(str(address), []).append(txid)
            addresses = list(dict.fromkeys([str(item) for item in signal.get("candidate_addresses", []) if item]))
            for address in addresses:
                item = grouped.setdefault(
                    address,
                    {
                        "address": address,
                        "confidence": 0.0,
                        "signals": [],
                        "txids": [],
                        "reasons": [],
                    },
                )
                confidence = _float(signal.get("confidence"))
                item["confidence"] = max(float(item.get("confidence") or 0), confidence)
                txids = list(dict.fromkeys([*linked_by_tx.get(address, []), *[str(txid) for txid in signal.get("txids", []) if txid]]))
                item["txids"] = list(dict.fromkeys([*item.get("txids", []), *txids]))[:10]
                item["reasons"] = list(dict.fromkeys([*item.get("reasons", []), *[str(reason) for reason in signal.get("reasons", []) if reason]]))[:8]
                item["signals"].append(
                    {
                        "id": signal.get("id"),
                        "title": signal.get("title"),
                        "url": signal.get("url"),
                        "published_at": signal.get("published_at"),
                        "confidence": confidence,
                        "btc_amounts": signal.get("btc_amounts") or [],
                        "eth_amounts": signal.get("eth_amounts") or [],
                        "usd_amounts": signal.get("usd_amounts") or [],
                        "txids": txids,
                        "behavior": _signal_behavior_label(signal),
                    }
                )
            large_matches = signal.get("large_transfer_matches") if isinstance(signal.get("large_transfer_matches"), list) else []
            for match in large_matches:
                if not isinstance(match, dict):
                    continue
                address = str(match.get("candidate_address") or "")
                if not address:
                    continue
                item = grouped.setdefault(
                    address,
                    {
                        "address": address,
                        "confidence": 0.0,
                        "signals": [],
                        "txids": [],
                        "reasons": [],
                    },
                )
                confidence = _float(match.get("confidence"))
                item["confidence"] = max(float(item.get("confidence") or 0), confidence)
                txid = str(match.get("txid") or "")
                if txid:
                    item["txids"] = list(dict.fromkeys([*item.get("txids", []), txid]))[:10]
                item["reasons"] = list(dict.fromkeys([*item.get("reasons", []), *[str(reason) for reason in match.get("reasons", []) if reason]]))[:10]
                operation = dict(match.get("transfer") or {})
                operation.update(
                    {
                        "address": address,
                        "txid": txid,
                        "timestamp": operation.get("block_time_utc"),
                        "timestamp_ms": _datetime_ms(str(operation.get("block_time_utc") or "")),
                        "amount_btc": match.get("address_value_btc") or operation.get("amount_btc"),
                        "direction": "out" if match.get("address_role") == "source" else "in",
                        "behavior": "转出" if match.get("address_role") == "source" else "转入",
                        "source_url": match.get("source_url"),
                    }
                )
                item["signals"].append(
                    {
                        "id": signal.get("id"),
                        "title": signal.get("title"),
                        "url": signal.get("url"),
                        "published_at": signal.get("published_at"),
                        "confidence": confidence,
                        "btc_amounts": signal.get("btc_amounts") or [],
                        "eth_amounts": signal.get("eth_amounts") or [],
                        "usd_amounts": signal.get("usd_amounts") or [],
                        "txids": [txid] if txid else [],
                        "behavior": match.get("behavior") or "底表交易与新闻行为相似",
                        "operation": operation,
                        "match_reasons": match.get("reasons") or [],
                        "large_transfer_match": match,
                    }
                )
        for address, operations in (candidate_activity or {}).items():
            item = grouped.setdefault(
                str(address),
                {
                    "address": str(address),
                    "confidence": 0.1,
                    "signals": [],
                    "txids": [],
                    "reasons": ["已加入疑似地址池"],
                },
            )
            item["latest_operations"] = sorted(operations, key=lambda operation: int(operation.get("timestamp_ms") or 0), reverse=True)[:10]
            for operation in operations:
                for signal in signals:
                    match_confidence, match_reasons = _operation_signal_match(operation, signal)
                    if match_confidence <= 0:
                        continue
                    confidence = min(0.95, max(_float(signal.get("confidence")), 0.25) + match_confidence)
                    item["confidence"] = max(float(item.get("confidence") or 0), confidence)
                    item["reasons"] = list(dict.fromkeys([*item.get("reasons", []), *match_reasons, *[str(reason) for reason in signal.get("reasons", []) if reason]]))[:10]
                    item["txids"] = list(dict.fromkeys([*item.get("txids", []), str(operation.get("txid") or "")]))[:10]
                    item["signals"].append(
                        {
                            "id": signal.get("id"),
                            "title": signal.get("title"),
                            "url": signal.get("url"),
                            "published_at": signal.get("published_at"),
                            "confidence": confidence,
                            "btc_amounts": signal.get("btc_amounts") or [],
                            "eth_amounts": signal.get("eth_amounts") or [],
                            "usd_amounts": signal.get("usd_amounts") or [],
                            "txids": [operation.get("txid")],
                            "behavior": f"链上{operation.get('behavior') or operation.get('direction')} {float(operation.get('amount_btc') or 0):g} BTC，与新闻行为相似",
                            "operation": operation,
                            "match_reasons": match_reasons,
                        }
                    )
        result = []
        for item in grouped.values():
            signals_for_address = sorted(item.get("signals", []), key=lambda signal: str(signal.get("published_at") or ""), reverse=True)
            item["signals"] = signals_for_address[:10]
            item["signal_count"] = len(signals_for_address)
            result.append(item)
        return sorted(result, key=lambda item: (float(item.get("confidence") or 0), int(item.get("signal_count") or 0)), reverse=True)

    def _get_text(self, url: str) -> str:
        try:
            response = requests.get(url, headers={"User-Agent": "CryptoMonitor/0.1"}, timeout=self.timeout_seconds)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            raise WhaleProviderError(f"请求失败: {url}: {exc}") from exc

    def _get_json(self, path: str) -> Any:
        errors: list[str] = []
        for base_url in [self.blockstream_base_url, *self.blockstream_fallback_base_urls]:
            try:
                response = requests.get(
                    f"{base_url}{path}",
                    headers={"accept": "application/json", "User-Agent": "CryptoMonitor/0.1"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                errors.append(f"{base_url}: {exc}")
                continue
        raise WhaleProviderError(f"Blockstream BTC request failed: {'; '.join(errors)}")

    @staticmethod
    def parse_news_feed(text: str, *, feed_url: str, keywords: list[str]) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise WhaleProviderError(f"新闻 RSS 解析失败: {exc}") from exc
        items = list(root.findall(".//item")) or [item for item in root.iter() if _xml_name(item.tag) == "entry"]
        normalized_keywords = [keyword.casefold() for keyword in keywords if keyword.strip()]
        signals: list[dict[str, Any]] = []
        for item in items:
            title = _xml_child_text(item, "title")
            link = _xml_child_text(item, "link")
            if not link:
                link = _xml_link_href(item)
            event_id = _xml_child_text(item, "guid") or _xml_child_text(item, "id") or link or title
            content_html = _xml_child_text(item, "description") or _xml_child_text(item, "summary") or _xml_child_text(item, "content")
            content = _html_lines(content_html)
            content_text = " ".join(content) if content else _html_to_plain_text(content_html)
            combined = f"{title}\n{content_text[:800]}\n{link}"
            if normalized_keywords and not any(keyword in combined.casefold() for keyword in normalized_keywords):
                continue
            if not _is_ibit_news_context(combined) and not _is_blackrock_eth_news_context(combined):
                continue
            txids = _extract_btc_txids(combined)
            btc_addresses = extract_btc_addresses(combined)
            evm_addresses = extract_addresses(combined)
            btc_amounts = _extract_btc_amounts(combined)
            eth_amounts = _extract_eth_amounts(combined)
            usd_amounts = _extract_usd_amounts(combined)
            published_at = (
                _xml_child_text(item, "pubDate")
                or _xml_child_text(item, "published")
                or _xml_child_text(item, "updated")
                or datetime.now(timezone.utc).isoformat()
            )
            signals.append(
                {
                    "id": _stable_id(feed_url, event_id or combined),
                    "source": _feed_source_label(feed_url),
                    "feed_url": feed_url,
                    "title": title or content_text[:120] or "IBIT 新闻线索",
                    "summary": content_text[:500],
                    "original_text": content_text[:4000],
                    "url": link or feed_url,
                    "published_at": _parse_feed_datetime(published_at).isoformat(),
                    "txids": txids,
                    "candidate_addresses": list(dict.fromkeys([*btc_addresses, *evm_addresses])),
                    "btc_addresses": btc_addresses,
                    "evm_addresses": evm_addresses,
                    "btc_amounts": btc_amounts,
                    "eth_amounts": eth_amounts,
                    "usd_amounts": usd_amounts,
                    "mentions_coinbase": "coinbase" in combined.casefold(),
                    "mentions_blackrock_or_ibit": any(keyword in combined.casefold() for keyword in ("ibit", "blackrock", "ishares bitcoin trust", "贝莱德")),
                }
            )
        return signals

    @staticmethod
    def parse_btc_tx_address_candidates(tx: dict[str, Any]) -> dict[str, Any]:
        txid = str(tx.get("txid") or "")
        if not txid:
            return {}
        input_addresses: list[str] = []
        output_items: list[dict[str, Any]] = []
        for vin in tx.get("vin", []) if isinstance(tx.get("vin"), list) else []:
            prevout = vin.get("prevout") if isinstance(vin, dict) and isinstance(vin.get("prevout"), dict) else {}
            address = str(prevout.get("scriptpubkey_address") or "")
            if address:
                input_addresses.append(address)
        for vout in tx.get("vout", []) if isinstance(tx.get("vout"), list) else []:
            if not isinstance(vout, dict):
                continue
            address = str(vout.get("scriptpubkey_address") or "")
            value_btc = _float(vout.get("value")) / 100_000_000
            if address and value_btc > 0:
                output_items.append({"address": address, "value_btc": value_btc})
        status = tx.get("status") if isinstance(tx.get("status"), dict) else {}
        block_time = _int(status.get("block_time"))
        return {
            "txid": txid,
            "input_addresses": list(dict.fromkeys(input_addresses))[:20],
            "output_addresses": sorted(output_items, key=lambda item: float(item["value_btc"]), reverse=True)[:20],
            "timestamp_ms": block_time * 1000 if block_time > 0 else None,
            "confirmed": bool(status.get("confirmed")),
            "source_url": f"https://blockstream.info/tx/{txid}",
        }

    @staticmethod
    def parse_btc_address_operation(tx: dict[str, Any], address: str) -> dict[str, Any] | None:
        txid = str(tx.get("txid") or "")
        if not txid:
            return None
        input_sat = 0
        output_sat = 0
        input_counterparties: list[str] = []
        output_counterparties: list[dict[str, Any]] = []
        for vin in tx.get("vin", []) if isinstance(tx.get("vin"), list) else []:
            prevout = vin.get("prevout") if isinstance(vin, dict) and isinstance(vin.get("prevout"), dict) else {}
            prev_address = str(prevout.get("scriptpubkey_address") or "")
            value = int(_float(prevout.get("value")))
            if prev_address == address:
                input_sat += value
            elif prev_address:
                input_counterparties.append(prev_address)
        for vout in tx.get("vout", []) if isinstance(tx.get("vout"), list) else []:
            if not isinstance(vout, dict):
                continue
            out_address = str(vout.get("scriptpubkey_address") or "")
            value = int(_float(vout.get("value")))
            if out_address == address:
                output_sat += value
            elif out_address and value > 0:
                output_counterparties.append({"address": out_address, "value_btc": value / 100_000_000})
        if input_sat <= 0 and output_sat <= 0:
            return None
        net_sat = output_sat - input_sat
        if net_sat > 0:
            direction = "in"
            amount_sat = net_sat
            behavior = "转入"
        elif net_sat < 0:
            direction = "out"
            amount_sat = abs(net_sat)
            behavior = "转出"
        else:
            direction = "neutral"
            amount_sat = max(input_sat, output_sat)
            behavior = "地址整理"
        status = tx.get("status") if isinstance(tx.get("status"), dict) else {}
        block_time = _int(status.get("block_time"))
        timestamp_ms = block_time * 1000 if block_time > 0 else _now_ms()
        return {
            "address": address,
            "txid": txid,
            "block_height": _int(status.get("block_height")),
            "block_hash": str(status.get("block_hash") or ""),
            "direction": direction,
            "behavior": behavior,
            "amount_btc": amount_sat / 100_000_000,
            "net_btc": net_sat / 100_000_000,
            "input_btc": input_sat / 100_000_000,
            "output_btc": output_sat / 100_000_000,
            "timestamp_ms": timestamp_ms,
            "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
            "confirmed": bool(status.get("confirmed")),
            "input_counterparties": list(dict.fromkeys(input_counterparties))[:5],
            "output_counterparties": sorted(output_counterparties, key=lambda item: float(item["value_btc"]), reverse=True)[:5],
            "source_url": f"https://blockstream.info/tx/{txid}",
        }

    @staticmethod
    def parse_ishares_page(text: str) -> dict[str, Any]:
        decoded = unescape(text or "")
        net_assets_metric = _ishares_metric(decoded, "totalNetAssetsFundLevel", "Net Assets of Fund")
        benchmark_metric = _ishares_metric(decoded, "levelAmount", "Benchmark Level")
        shares_metric = _ishares_metric(decoded, "sharesOutstanding", "Shares Outstanding")
        basket_btc_metric = _ishares_metric(decoded, "basketAmt", "Basket Bitcoin Amount")
        indicative_basket_btc_metric = _ishares_metric(decoded, "indicativeBasketAmt", "Indicative Basket Bitcoin Amount")
        lines = _html_lines(text)
        net_assets = net_assets_metric["value"] if net_assets_metric["value"] is not None else _number_after_label(lines, "Net Assets of Fund")
        benchmark = benchmark_metric["value"] if benchmark_metric["value"] is not None else _number_after_label(lines, "Benchmark Level")
        shares = shares_metric["value"] if shares_metric["value"] is not None else _number_after_label(lines, "Shares Outstanding")
        basket_btc = basket_btc_metric["value"] if basket_btc_metric["value"] is not None else _number_after_label(lines, "Basket Bitcoin Amount")
        indicative_basket_btc = (
            indicative_basket_btc_metric["value"]
            if indicative_basket_btc_metric["value"] is not None
            else _number_after_label(lines, "Indicative Basket Bitcoin Amount")
        )
        as_of = (
            net_assets_metric["as_of"]
            or shares_metric["as_of"]
            or _date_after_label(lines, "Net Assets of Fund")
            or _date_after_label(lines, "Shares Outstanding")
        )
        result = {
            "as_of": as_of,
            "net_assets": net_assets,
            "benchmark_price": benchmark,
            "shares_outstanding": shares,
            "basket_btc": basket_btc,
            "indicative_basket_btc": indicative_basket_btc,
        }
        if net_assets is not None and benchmark:
            result["estimated_btc_holdings"] = net_assets / benchmark
        return {key: value for key, value in result.items() if value not in (None, "")}

    @staticmethod
    def parse_farside_flows(text: str) -> dict[str, Any]:
        lines = _html_lines(text)
        try:
            header_start = lines.index("Total")
            fee_index = lines.index("Fee", header_start)
        except ValueError:
            return {}
        headers = lines[header_start:fee_index]
        if "IBIT" not in headers:
            return {}
        ibit_index = headers.index("IBIT")
        rows: list[dict[str, Any]] = []
        index = fee_index + len(headers) + 1
        while index < len(lines):
            line = lines[index]
            if line in {"Total", "Average", "Maximum", "Minimum"}:
                break
            if _looks_like_date(line):
                values: list[float | None] = []
                cursor = index + 1
                while cursor < len(lines) and len(values) < len(headers):
                    value = _parse_number(lines[cursor])
                    if value is not None:
                        values.append(value)
                    cursor += 1
                if len(values) >= len(headers):
                    rows.append({"date": line, "ibit_flow_usd_m": values[ibit_index], "values": dict(zip(headers, values))})
                    index = cursor
                    continue
            index += 1
        return rows[0] if rows else {}

    @staticmethod
    def parse_btc_cluster_transfer(tx: dict[str, Any], watched: set[str]) -> dict[str, Any] | None:
        txid = str(tx.get("txid") or "")
        if not txid:
            return None
        input_sat = 0
        output_sat = 0
        external_outputs: list[dict[str, Any]] = []
        for vin in tx.get("vin", []) if isinstance(tx.get("vin"), list) else []:
            prevout = vin.get("prevout") if isinstance(vin, dict) and isinstance(vin.get("prevout"), dict) else {}
            if str(prevout.get("scriptpubkey_address") or "") in watched:
                input_sat += int(_float(prevout.get("value")))
        for vout in tx.get("vout", []) if isinstance(tx.get("vout"), list) else []:
            if not isinstance(vout, dict):
                continue
            address = str(vout.get("scriptpubkey_address") or "")
            value = int(_float(vout.get("value")))
            if address in watched:
                output_sat += value
            elif value > 0:
                external_outputs.append({"address": address, "value_btc": value / 100_000_000})
        net_sat = output_sat - input_sat
        if net_sat >= 0:
            return None
        status = tx.get("status") if isinstance(tx.get("status"), dict) else {}
        block_time = _int(status.get("block_time"))
        timestamp_ms = block_time * 1000 if block_time > 0 else _now_ms()
        return {
            "txid": txid,
            "direction": "out",
            "amount_btc": abs(net_sat) / 100_000_000,
            "timestamp_ms": timestamp_ms,
            "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
            "confirmed": bool(status.get("confirmed")),
            "external_outputs": sorted(external_outputs, key=lambda item: float(item["value_btc"]), reverse=True)[:5],
            "source_url": f"https://blockstream.info/tx/{txid}",
        }


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


def _int(value: Any, fallback: int = 0) -> int:
    try:
        if value in (None, ""):
            return fallback
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _html_lines(text: str) -> list[str]:
    cleaned = re.sub(r"(?is)<(script|style).*?</\1>", " ", text or "")
    cleaned = re.sub(r"(?s)<[^>]+>", "\n", cleaned)
    lines = [unescape(line).replace("\xa0", " ").strip() for line in cleaned.splitlines()]
    return [line for line in lines if line]


def _html_to_plain_text(text: str) -> str:
    return " ".join(_html_lines(text))


def _xml_name(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _xml_child_text(item: ET.Element, name: str) -> str:
    for child in item:
        if _xml_name(child.tag) == name and child.text:
            return unescape(str(child.text)).strip()
    return ""


def _xml_link_href(item: ET.Element) -> str:
    for child in item:
        if _xml_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
    return ""


def _parse_feed_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _extract_btc_txids(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0).lower() for match in BTC_TXID_RE.finditer(text or "")))


def _is_ibit_news_context(text: str) -> bool:
    lower = (text or "").casefold()
    direct_terms = (
        "ibit",
        "ishares bitcoin trust",
        "bitcoin etf",
        "spot bitcoin etf",
        "比特币etf",
        "比特币 ETF".casefold(),
        "比特币现货",
    )
    if any(term.casefold() in lower for term in direct_terms):
        return True
    issuer_terms = ("blackrock", "贝莱德")
    btc_context_terms = ("btc", "bitcoin", "比特币", "coinbase", "onchainlens", "lookonchain", "arkham", "txid")
    if any(term in lower for term in issuer_terms) and any(term in lower for term in btc_context_terms):
        return True
    return False


def _is_blackrock_eth_news_context(text: str) -> bool:
    lower = (text or "").casefold()
    issuer_terms = ("blackrock", "贝莱德")
    eth_terms = ("eth", "ethereum", "以太坊")
    chain_terms = ("coinbase", "onchainlens", "lookonchain", "arkham", "txid", "address", "地址")
    return any(term in lower for term in issuer_terms) and any(term in lower for term in eth_terms) and any(term in lower for term in chain_terms)


def _extract_btc_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for match in re.finditer(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:枚|个|顆|颗)?\s*(?:BTC|比特币)", text or "", re.IGNORECASE):
        value = _parse_number(match.group(1))
        if value is not None:
            amounts.append(value)
    return _dedupe_numbers([*amounts, *_extract_asset_amounts(text, ("BTC", "Bitcoin"))])


def _extract_eth_amounts(text: str) -> list[float]:
    return _extract_asset_amounts(text, ("ETH", "Ethereum", "以太坊"))


def _extract_asset_amounts(text: str, symbols: tuple[str, ...]) -> list[float]:
    amounts: list[float] = []
    symbol_pattern = "|".join(re.escape(symbol) for symbol in symbols)
    pattern = rf"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:枚|个|颗|coins?|tokens?)?\s*(?:{symbol_pattern})\b"
    for match in re.finditer(pattern, text or "", re.IGNORECASE):
        value = _parse_number(match.group(1))
        if value is not None:
            amounts.append(value)
    return _dedupe_numbers(amounts)


def _extract_usd_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for match in re.finditer(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿|万)?\s*(?:美元|美金)", text or "", re.IGNORECASE):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        unit = match.group(2)
        multiplier = 100_000_000 if unit == "亿" else 10_000 if unit == "万" else 1
        amounts.append(value * multiplier)
    for match in re.finditer(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|bn|million|m)?\s*(?:usd|dollars?)?", text or "", re.IGNORECASE):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        unit = (match.group(2) or "").lower()
        if unit in {"billion", "bn"}:
            value *= 1_000_000_000
        elif unit in {"million", "m"}:
            value *= 1_000_000
        elif "$" not in match.group(0).lower() and "usd" not in match.group(0).lower() and "dollar" not in match.group(0).lower():
            continue
        amounts.append(value)
    return _dedupe_numbers(amounts)


def _dedupe_numbers(values: list[float]) -> list[float]:
    result: list[float] = []
    seen: set[str] = set()
    for value in values:
        key = f"{value:.8f}"
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _score_ibit_news_signal(signal: dict[str, Any], expected_flow_usd: float | None = None) -> tuple[float, list[str]]:
    score = 0.15
    reasons: list[str] = []
    if signal.get("mentions_blackrock_or_ibit"):
        score += 0.2
        reasons.append("命中 IBIT/发行方关键词")
    if signal.get("mentions_coinbase"):
        score += 0.15
        reasons.append("提到 Coinbase")
    if signal.get("txids"):
        score += 0.2
        reasons.append("包含 txid")
    if signal.get("candidate_addresses"):
        score += 0.2
        reasons.append("包含 BTC 地址")
    if signal.get("btc_amounts") or signal.get("eth_amounts") or signal.get("usd_amounts"):
        score += 0.1
        reasons.append("包含 BTC/USD 金额")
    if signal.get("tx_candidates"):
        score += 0.1
        reasons.append("txid 可反查链上地址")
    if expected_flow_usd:
        closest = min((_relative_delta(expected_flow_usd, _float(value)) for value in signal.get("usd_amounts", [])), default=None)
        if closest is not None and closest <= 0.5:
            score += 0.1
            reasons.append("金额接近最新 IBIT 资金流")
    return min(score, 0.95), reasons


def _signal_behavior_label(signal: dict[str, Any]) -> str:
    text = f"{signal.get('title') or ''}\n{signal.get('summary') or ''}".casefold()
    if "coinbase" in text and any(term in text for term in ("deposit", "deposited", "存入", "转入", "流入")):
        return "新闻称向 Coinbase 存入/转入"
    if "coinbase" in text:
        return "新闻提到 Coinbase 相关转账"
    if any(term in text for term in ("outflow", "withdraw", "转出", "流出")):
        return "新闻称发生转出/流出"
    if any(term in text for term in ("inflow", "deposit", "买入", "流入", "存入")):
        return "新闻称发生流入/买入"
    if signal.get("txids"):
        return "新闻给出 txid 线索"
    if signal.get("candidate_addresses"):
        return "新闻给出 BTC 地址线索"
    return "新闻提到 IBIT/比特币 ETF 相关动作"


def _operation_signal_match(operation: dict[str, Any], signal: dict[str, Any]) -> tuple[float, list[str]]:
    confidence = 0.0
    reasons: list[str] = []
    operation_time = _parse_feed_datetime(str(operation.get("timestamp") or ""))
    signal_time = _parse_feed_datetime(str(signal.get("published_at") or ""))
    delta_hours = abs((signal_time - operation_time).total_seconds()) / 3600
    if delta_hours <= 48:
        confidence += 0.1
        reasons.append(f"链上操作与新闻时间相差 {delta_hours:.1f} 小时")

    amount = abs(_float(operation.get("amount_btc")))
    closest_btc_delta = min((_relative_delta(amount, _float(value)) for value in signal.get("btc_amounts", []) if _float(value) > 0), default=None)
    if closest_btc_delta is not None and closest_btc_delta <= 0.25:
        confidence += 0.25
        reasons.append("BTC 数量接近新闻金额")
    elif closest_btc_delta is not None and closest_btc_delta <= 0.5:
        confidence += 0.12
        reasons.append("BTC 数量大致接近新闻金额")

    text = f"{signal.get('title') or ''}\n{signal.get('summary') or ''}".casefold()
    direction = str(operation.get("direction") or "")
    if direction == "out" and any(term in text for term in ("deposit", "deposited", "存入", "转出", "流出", "向 coinbase")):
        confidence += 0.1
        reasons.append("链上转出方向与新闻行为一致")
    if direction == "in" and any(term in text for term in ("inflow", "receive", "收到", "买入", "流入")):
        confidence += 0.1
        reasons.append("链上转入方向与新闻行为一致")
    if signal.get("mentions_coinbase") and direction == "out":
        confidence += 0.05
        reasons.append("新闻提到 Coinbase，地址发生转出")

    if confidence < 0.2:
        return 0.0, []
    return min(confidence, 0.5), reasons


def _relative_delta(left: float, right: float) -> float:
    base = max(abs(left), abs(right), 1.0)
    return abs(left - right) / base


def _datetime_ms(value: str) -> int:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return _now_ms()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _stable_id(namespace: str, value: str) -> str:
    return hashlib.sha1(f"{namespace}|{value}".encode("utf-8", errors="ignore")).hexdigest()


def _feed_source_label(feed_url: str) -> str:
    host = re.sub(r"^www\.", "", urlparse(feed_url).netloc)
    return host or "IBIT News"


def _ishares_metric(text: str, key: str, label: str) -> dict[str, Any]:
    chunk = _ishares_metric_chunk(text, f'"{key}"')
    if not chunk:
        chunk = _ishares_metric_chunk(text, f'"label":"{label}"')
    formatted = _json_string_field(chunk, "formattedValue")
    raw = _json_string_field(chunk, "value")
    return {
        "value": _parse_number(formatted) if formatted is not None else _parse_number(raw),
        "as_of": _json_string_field(chunk, "formattedAsOfDate") or "",
    }


def _ishares_metric_chunk(text: str, marker: str, *, before: int = 0) -> str:
    index = text.find(marker)
    if index < 0:
        return ""
    start = max(0, index - before)
    return text[start:index + 3000]


def _json_string_field(text: str, field: str) -> str | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"', text or "")
    if not match:
        return None
    return match.group(1).replace(r"\/", "/").strip()


def _number_after_label(lines: list[str], label: str) -> float | None:
    for index, line in enumerate(lines):
        if line.strip().lower() != label.lower():
            continue
        for candidate in lines[index + 1:index + 8]:
            value = _parse_number(candidate)
            if value is not None:
                return value
    return None


def _date_after_label(lines: list[str], label: str) -> str:
    for index, line in enumerate(lines):
        if line.strip().lower() != label.lower():
            continue
        for candidate in lines[index + 1:index + 10]:
            text = candidate.strip()
            if text.lower().startswith("as of "):
                return text[6:].strip()
            if _looks_like_date(text):
                return text
    return ""


def _looks_like_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", value.strip()) or re.fullmatch(r"[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}", value.strip()))


def _parse_number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"-", "--"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        number = float(cleaned)
        return -abs(number) if negative else number
    except ValueError:
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
