from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from .rate_limit import wait_for_host_rate_limit


SATOSHI = 100_000_000
DEFAULT_BLOCKSTREAM_BASE_URL = "https://blockstream.info/api"
FUTURE_MATCH_GRACE = timedelta(minutes=5)


class BtcLargeTransferError(RuntimeError):
    pass


class BtcLargeTransferProvider:
    source_name = "btc_large_transfers"
    label = "BTC Large Transfers"

    def __init__(self, base_url: str = DEFAULT_BLOCKSTREAM_BASE_URL, timeout_seconds: int = 20, min_request_interval_seconds: float = 0.6) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.min_request_interval_seconds = max(0.0, float(min_request_interval_seconds))

    def latest_height(self) -> int:
        text = self._get_text("/blocks/tip/height")
        try:
            return int(text.strip())
        except ValueError as exc:
            raise BtcLargeTransferError(f"Blockstream latest height invalid: {text[:80]}") from exc

    def block_summary(self, height: int) -> dict[str, Any]:
        block_hash = self._get_text(f"/block-height/{int(height)}").strip()
        if not block_hash:
            raise BtcLargeTransferError(f"Blockstream block hash missing for height {height}")
        block = self._get_json(f"/block/{quote(block_hash, safe='')}")
        if not isinstance(block, dict):
            raise BtcLargeTransferError(f"Blockstream block summary invalid for height {height}")
        return block

    def height_at_or_before(self, timestamp_utc: int) -> int:
        latest = self.latest_height()
        low = 0
        high = latest
        while low < high:
            mid = (low + high + 1) // 2
            block = self.block_summary(mid)
            block_timestamp = int(block.get("timestamp") or 0)
            if block_timestamp <= timestamp_utc:
                low = mid
            else:
                high = mid - 1
        return low

    def scan_block(self, height: int, *, min_btc: float) -> list[dict[str, Any]]:
        block = self.block_summary(height)
        block_hash = str(block.get("id") or "")
        tx_count = int(block.get("tx_count") or 0) if isinstance(block, dict) else 0
        timestamp = int(block.get("timestamp") or 0) if isinstance(block, dict) else 0
        transfers: list[dict[str, Any]] = []
        start = 0
        while start < tx_count:
            page = self._get_json(f"/block/{quote(block_hash, safe='')}/txs/{start}")
            if not isinstance(page, list) or not page:
                break
            for tx in page:
                if not isinstance(tx, dict):
                    continue
                parsed = parse_btc_large_transfer(tx, block_height=height, block_hash=block_hash, block_timestamp=timestamp, min_btc=min_btc)
                if parsed:
                    transfers.append(parsed)
            start += len(page)
        return transfers

    def _get_text(self, path: str) -> str:
        response = self._request(path, accept="text/plain")
        return response.text

    def _get_json(self, path: str) -> Any:
        response = self._request(path, accept="application/json")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise BtcLargeTransferError(f"Blockstream BTC request failed: {exc}") from exc

    def _request(self, path: str, *, accept: str) -> requests.Response:
        last_error: requests.RequestException | None = None
        for attempt in range(5):
            try:
                self._wait_for_rate_limit()
                response = requests.get(
                    f"{self.base_url}{path}",
                    headers={"accept": accept, "User-Agent": "CryptoMonitor/0.1"},
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429 and attempt < 4:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(1.2 * (attempt + 1))
                    continue
        raise BtcLargeTransferError(f"Blockstream BTC request failed: {last_error}") from last_error

    def _wait_for_rate_limit(self) -> None:
        wait_for_host_rate_limit(self.base_url, self.min_request_interval_seconds)


def parse_btc_large_transfer(
    tx: dict[str, Any],
    *,
    block_height: int,
    block_hash: str,
    block_timestamp: int,
    min_btc: float,
) -> dict[str, Any] | None:
    txid = str(tx.get("txid") or "")
    if not txid:
        return None
    input_values = _aggregate_inputs(tx)
    output_values = _aggregate_outputs(tx)
    total_input_btc = sum(input_values.values()) / SATOSHI
    total_output_btc = sum(output_values.values()) / SATOSHI
    input_addresses = _address_items(input_values)
    output_addresses = _address_items(output_values)
    largest_input_btc = float(input_addresses[0]["value_btc"]) if input_addresses else 0.0
    largest_output_btc = float(output_addresses[0]["value_btc"]) if output_addresses else 0.0
    threshold_amount = max(total_input_btc, total_output_btc, largest_input_btc, largest_output_btc)
    if threshold_amount < float(min_btc):
        return None
    status = tx.get("status") if isinstance(tx.get("status"), dict) else {}
    block_time = int(status.get("block_time") or block_timestamp or 0)
    fee_btc = float(tx.get("fee") or 0) / SATOSHI
    if fee_btc <= 0 and total_input_btc > total_output_btc:
        fee_btc = total_input_btc - total_output_btc
    return {
        "txid": txid,
        "chain": "btc",
        "asset": "BTC",
        "block_height": int(status.get("block_height") or block_height or 0),
        "block_hash": str(status.get("block_hash") or block_hash or ""),
        "block_time_utc": _timestamp_iso(block_time),
        "amount": largest_output_btc or total_output_btc or threshold_amount,
        "amount_btc": largest_output_btc or total_output_btc or threshold_amount,
        "total_input_amount": total_input_btc,
        "total_output_amount": total_output_btc,
        "fee_amount": fee_btc,
        "total_input_btc": total_input_btc,
        "total_output_btc": total_output_btc,
        "fee_btc": fee_btc,
        "input_addresses": input_addresses[:80],
        "output_addresses": output_addresses[:80],
        "address_operations": _address_operations(input_values, output_values)[:120],
        "exchange_hints": _exchange_hints(tx),
        "source_url": f"https://blockstream.info/tx/{txid}",
        "raw": {
            "size": tx.get("size"),
            "weight": tx.get("weight"),
            "fee": tx.get("fee"),
            "status": status,
        },
    }


def match_news_signal_to_large_transfers(
    signal: dict[str, Any],
    transfers: list[dict[str, Any]],
    *,
    window_hours: int,
    amount_tolerance_pct: float,
    max_matches: int,
) -> list[dict[str, Any]]:
    signal_time = _parse_datetime(str(signal.get("published_at") or ""))
    role = _preferred_address_role(signal)
    results: list[dict[str, Any]] = []
    for transfer in transfers:
        transfer_time = _parse_datetime(str(transfer.get("block_time_utc") or ""))
        delta_hours = abs((signal_time - transfer_time).total_seconds()) / 3600
        if delta_hours > max(1, window_hours):
            continue
        txid_exact_match = _signal_mentions_txid(signal, str(transfer.get("txid") or ""))
        if not txid_exact_match and transfer_time > signal_time + FUTURE_MATCH_GRACE:
            continue
        amount_score, amount_reasons, amount_delta_pct = _amount_match(signal, transfer, amount_tolerance_pct)
        if amount_score <= 0:
            continue
        asset = str(transfer.get("asset") or "BTC").upper()
        if asset != "BTC":
            amount_reasons = [reason.replace("BTC", asset) for reason in amount_reasons]
        time_score, time_reason = _time_score(delta_hours)
        keyword_score = 0.0
        reasons = [time_reason, *amount_reasons]
        if signal.get("mentions_blackrock_or_ibit"):
            keyword_score += 0.08
            reasons.append("新闻命中 IBIT/贝莱德关键词")
        if signal.get("mentions_coinbase"):
            keyword_score += 0.08
            reasons.append("新闻提到 Coinbase")
        if role != "unknown":
            keyword_score += 0.12
            reasons.append("新闻方向可映射到链上地址角色")
        confidence = min(0.95, 0.18 + time_score + amount_score + keyword_score)
        if confidence < 0.35:
            continue
        chain = str(transfer.get("chain") or "btc").lower()
        for address in _candidate_addresses_for_role(transfer, role):
            address_value = address.get("value")
            results.append(
                {
                    "txid": transfer.get("txid"),
                    "chain": chain,
                    "asset": asset,
                    "candidate_address": address.get("address"),
                    "address_role": address.get("role"),
                    "address_value": address_value,
                    "address_value_btc": address_value if asset == "BTC" else None,
                    "address_value_eth": address_value if asset == "ETH" else None,
                    "confidence": confidence,
                    "reasons": list(dict.fromkeys(reasons)),
                    "delta_hours": delta_hours,
                    "amount_delta_pct": amount_delta_pct,
                    "behavior": _match_behavior_label(signal, role, asset),
                    "source_url": transfer.get("source_url"),
                    "transfer": {
                        "txid": transfer.get("txid"),
                        "chain": chain,
                        "asset": asset,
                        "block_time_utc": transfer.get("block_time_utc"),
                        "amount": transfer.get("amount"),
                        "amount_btc": transfer.get("amount_btc"),
                        "total_input_amount": transfer.get("total_input_amount"),
                        "total_output_amount": transfer.get("total_output_amount"),
                        "total_input_btc": transfer.get("total_input_btc"),
                        "total_output_btc": transfer.get("total_output_btc"),
                        "source_url": transfer.get("source_url"),
                    },
                }
            )
    return sorted(results, key=lambda item: (float(item.get("confidence") or 0), -float(item.get("delta_hours") or 0)), reverse=True)[: max(1, max_matches)]


def _aggregate_inputs(tx: dict[str, Any]) -> dict[str, int]:
    values: dict[str, int] = {}
    for vin in tx.get("vin", []) if isinstance(tx.get("vin"), list) else []:
        prevout = vin.get("prevout") if isinstance(vin, dict) and isinstance(vin.get("prevout"), dict) else {}
        address = str(prevout.get("scriptpubkey_address") or "")
        value = int(float(prevout.get("value") or 0))
        if address and value > 0:
            values[address] = values.get(address, 0) + value
    return values


def _aggregate_outputs(tx: dict[str, Any]) -> dict[str, int]:
    values: dict[str, int] = {}
    for vout in tx.get("vout", []) if isinstance(tx.get("vout"), list) else []:
        if not isinstance(vout, dict):
            continue
        address = str(vout.get("scriptpubkey_address") or "")
        value = int(float(vout.get("value") or 0))
        if address and value > 0:
            values[address] = values.get(address, 0) + value
    return values


def _address_items(values: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"address": address, "value": sat / SATOSHI, "value_btc": sat / SATOSHI}
        for address, sat in sorted(values.items(), key=lambda item: item[1], reverse=True)
    ]


def _address_operations(input_values: dict[str, int], output_values: dict[str, int]) -> list[dict[str, Any]]:
    addresses = set(input_values) | set(output_values)
    operations = []
    for address in addresses:
        net_sat = output_values.get(address, 0) - input_values.get(address, 0)
        if net_sat > 0:
            direction = "in"
            behavior = "转入"
        elif net_sat < 0:
            direction = "out"
            behavior = "转出"
        else:
            direction = "neutral"
            behavior = "地址整理"
        operations.append(
            {
                "address": address,
                "direction": direction,
                "behavior": behavior,
                "asset": "BTC",
                "amount": abs(net_sat) / SATOSHI,
                "amount_btc": abs(net_sat) / SATOSHI,
                "input_amount": input_values.get(address, 0) / SATOSHI,
                "output_amount": output_values.get(address, 0) / SATOSHI,
                "input_btc": input_values.get(address, 0) / SATOSHI,
                "output_btc": output_values.get(address, 0) / SATOSHI,
            }
        )
    return sorted(operations, key=lambda item: float(item["amount_btc"]), reverse=True)


def _exchange_hints(tx: dict[str, Any]) -> list[str]:
    text = json.dumps(tx, ensure_ascii=False).casefold()
    hints = []
    for name in ("coinbase", "binance", "okx", "kraken", "bitfinex", "bitstamp"):
        if name in text:
            hints.append(name)
    return hints


def _candidate_addresses_for_role(transfer: dict[str, Any], role: str) -> list[dict[str, Any]]:
    asset = str(transfer.get("asset") or "BTC").upper()
    if role == "receiver":
        rows = transfer.get("output_addresses") if isinstance(transfer.get("output_addresses"), list) else []
        label = "receiver"
    else:
        rows = transfer.get("input_addresses") if isinstance(transfer.get("input_addresses"), list) else []
        label = "source"
    result = []
    for row in rows[:5]:
        if not isinstance(row, dict) or not row.get("address"):
            continue
        value = _asset_amount(row, asset)
        result.append({"address": row.get("address"), "value": value, f"value_{asset.lower()}": value, "role": label})
    return result


def _preferred_address_role(signal: dict[str, Any]) -> str:
    text = f"{signal.get('title') or ''}\n{signal.get('summary') or ''}".casefold()
    if "coinbase" in text and any(term in text for term in ("存入", "转入", "deposit", "deposited", "to coinbase", "向 coinbase")):
        return "source"
    if "coinbase" in text and any(term in text for term in ("提出", "提取", "转出", "withdraw", "withdrawal", "from coinbase", "从 coinbase")):
        return "receiver"
    if any(term in text for term in ("存入", "转入", "deposit", "deposited")):
        return "source"
    if any(term in text for term in ("提出", "提取", "withdraw", "receive", "收到")):
        return "receiver"
    return "unknown"


def _amount_match(signal: dict[str, Any], transfer: dict[str, Any], tolerance_pct: float) -> tuple[float, list[str], float | None]:
    asset = str(transfer.get("asset") or "BTC").upper()
    signal_amounts = [float(value) for value in signal.get(f"{asset.lower()}_amounts", []) if float(value or 0) > 0]
    if not signal_amounts:
        return 0.0, [], None
    candidates = [
        _safe_float(transfer.get("amount")),
        _safe_float(transfer.get("total_input_amount")),
        _safe_float(transfer.get("total_output_amount")),
    ]
    if asset == "BTC":
        candidates.extend([
            _safe_float(transfer.get("amount_btc")),
            _safe_float(transfer.get("total_input_btc")),
            _safe_float(transfer.get("total_output_btc")),
        ])
    for row in transfer.get("output_addresses", []) if isinstance(transfer.get("output_addresses"), list) else []:
        if isinstance(row, dict):
            candidates.append(_asset_amount(row, asset))
    best_delta = min((_relative_delta(left, right) for left in signal_amounts for right in candidates if right > 0), default=None)
    if best_delta is None:
        return 0.0, [], None
    tolerance = max(0.01, float(tolerance_pct) / 100)
    if best_delta <= tolerance:
        return 0.38, [f"BTC 数量与新闻接近，偏差 {best_delta * 100:.1f}%"], best_delta * 100
    if best_delta <= tolerance * 2:
        return 0.25, [f"BTC 数量大致接近新闻，偏差 {best_delta * 100:.1f}%"], best_delta * 100
    if best_delta <= 0.25:
        return 0.12, [f"BTC 数量存在弱匹配，偏差 {best_delta * 100:.1f}%"], best_delta * 100
    return 0.0, [], best_delta * 100


def _asset_amount(row: dict[str, Any], asset: str) -> float:
    for key in (f"value_{asset.lower()}", "value", "amount", "value_btc"):
        value = _safe_float(row.get(key))
        if value > 0:
            return value
    return 0.0


def _safe_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _time_score(delta_hours: float) -> tuple[float, str]:
    if delta_hours <= 1:
        return 0.24, f"链上交易与新闻时间相差 {delta_hours:.1f} 小时"
    if delta_hours <= 6:
        return 0.2, f"链上交易与新闻时间相差 {delta_hours:.1f} 小时"
    if delta_hours <= 24:
        return 0.14, f"链上交易与新闻时间相差 {delta_hours:.1f} 小时"
    return 0.08, f"链上交易与新闻时间相差 {delta_hours:.1f} 小时"


def _match_behavior_label(signal: dict[str, Any], role: str, asset: str = "BTC") -> str:
    if asset != "BTC" and role == "unknown":
        return f"News behavior matched: large {asset} transfer time and amount are close"
    if role == "source":
        return "新闻行为匹配：疑似源地址发生大额转出"
    if role == "receiver":
        return "新闻行为匹配：疑似接收地址发生大额转入"
    return "新闻行为匹配：大额 BTC 交易时间和金额相近"


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_iso(value: int) -> str:
    if value <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _relative_delta(left: float, right: float) -> float:
    base = max(abs(left), abs(right), 1.0)
    return abs(left - right) / base


def normalize_btc_txid(value: str) -> str:
    text = value.strip()
    return text if re.fullmatch(r"[a-fA-F0-9]{64}", text) else ""


def _signal_mentions_txid(signal: dict[str, Any], txid: str) -> bool:
    normalized = normalize_btc_txid(txid)
    if not normalized:
        return False
    for value in signal.get("txids", []) if isinstance(signal.get("txids"), list) else []:
        if normalize_btc_txid(str(value)) == normalized:
            return True
    return False
