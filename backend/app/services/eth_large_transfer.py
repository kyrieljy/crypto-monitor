from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import requests


WEI = 1_000_000_000_000_000_000
DEFAULT_ETHERSCAN_BASE_URL = "https://api.etherscan.io/v2/api"
DEFAULT_ETHERSCAN_MIN_REQUEST_INTERVAL_SECONDS = 0.25


class EthLargeTransferError(RuntimeError):
    pass


class EthLargeTransferProvider:
    source_name = "eth_large_transfers"
    label = "ETH Large Transfers"

    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str = DEFAULT_ETHERSCAN_BASE_URL,
        chain_id: str = "1",
        timeout_seconds: int = 20,
        min_request_interval_seconds: float = DEFAULT_ETHERSCAN_MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.chain_id = str(chain_id or "1")
        self.timeout_seconds = timeout_seconds
        self.min_request_interval_seconds = max(0.0, float(min_request_interval_seconds or 0.0))
        self._last_request_at = 0.0

    def latest_height(self) -> int:
        payload = self._get_json({"module": "proxy", "action": "eth_blockNumber"})
        result = str(payload.get("result") or "")
        try:
            return int(result, 16)
        except ValueError as exc:
            raise EthLargeTransferError(f"Etherscan latest height invalid: {result[:80]}") from exc

    def height_at_or_before(self, timestamp_utc: int) -> int:
        payload = self._get_json(
            {
                "module": "block",
                "action": "getblocknobytime",
                "timestamp": str(int(timestamp_utc)),
                "closest": "before",
            }
        )
        result = payload.get("result")
        try:
            return int(result)
        except (TypeError, ValueError) as exc:
            raise EthLargeTransferError(f"Etherscan timestamp block invalid: {str(result)[:80]}") from exc

    def scan_block(self, height: int, *, min_eth: float) -> list[dict[str, Any]]:
        payload = self._get_json(
            {
                "module": "proxy",
                "action": "eth_getBlockByNumber",
                "tag": hex(int(height)),
                "boolean": "true",
            }
        )
        block = payload.get("result")
        if not isinstance(block, dict):
            raise EthLargeTransferError(f"Etherscan block payload invalid for height {height}")
        block_hash = str(block.get("hash") or "")
        block_number = _hex_int(block.get("number"), height)
        timestamp = _hex_int(block.get("timestamp"), 0)
        transfers: list[dict[str, Any]] = []
        for tx in block.get("transactions", []) if isinstance(block.get("transactions"), list) else []:
            if not isinstance(tx, dict):
                continue
            parsed = parse_eth_large_transfer(
                tx,
                block_height=block_number,
                block_hash=block_hash,
                block_timestamp=timestamp,
                min_eth=min_eth,
            )
            if parsed:
                transfers.append(parsed)
        return transfers

    def _get_json(self, params: dict[str, str]) -> dict[str, Any]:
        request_params = {
            "chainid": self.chain_id,
            "apikey": self.api_key,
            **params,
        }
        last_error: requests.RequestException | None = None
        for attempt in range(5):
            try:
                self._throttle()
                response = requests.get(
                    self.base_url,
                    params=request_params,
                    headers={"accept": "application/json", "User-Agent": "CryptoMonitor/0.1"},
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 429 and attempt < 4:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise EthLargeTransferError("Etherscan response is not a JSON object")
                message = str(payload.get("message") or "")
                result = payload.get("result")
                if message.upper() == "NOTOK" and not isinstance(result, (dict, list)):
                    raise EthLargeTransferError(f"Etherscan request failed: {str(result)[:300]}")
                return payload
            except EthLargeTransferError:
                raise
            except (requests.RequestException, json.JSONDecodeError) as exc:
                last_error = exc if isinstance(exc, requests.RequestException) else None
                if attempt < 4:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise EthLargeTransferError(f"Etherscan request failed: {exc}") from exc
        raise EthLargeTransferError(f"Etherscan request failed: {last_error}") from last_error

    def _throttle(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_at
        wait_seconds = self.min_request_interval_seconds - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self._last_request_at = time.monotonic()


def parse_eth_large_transfer(
    tx: dict[str, Any],
    *,
    block_height: int,
    block_hash: str,
    block_timestamp: int,
    min_eth: float,
) -> dict[str, Any] | None:
    tx_hash = str(tx.get("hash") or "")
    if not tx_hash:
        return None
    value_wei = _hex_int(tx.get("value"), 0)
    amount_eth = value_wei / WEI
    if amount_eth < float(min_eth):
        return None
    from_address = str(tx.get("from") or "").lower()
    to_address = str(tx.get("to") or "").lower()
    if not from_address or not to_address:
        return None
    fee_eth = (_hex_int(tx.get("gas"), 0) * _hex_int(tx.get("gasPrice"), 0)) / WEI
    input_addresses = [{"address": from_address, "value": amount_eth, "value_eth": amount_eth}]
    output_addresses = [{"address": to_address, "value": amount_eth, "value_eth": amount_eth}]
    return {
        "txid": tx_hash,
        "chain": "eth",
        "asset": "ETH",
        "block_height": int(block_height or 0),
        "block_hash": str(block_hash or ""),
        "block_time_utc": _timestamp_iso(int(block_timestamp or 0)),
        "amount": amount_eth,
        "amount_btc": 0.0,
        "amount_eth": amount_eth,
        "total_input_amount": amount_eth,
        "total_output_amount": amount_eth,
        "fee_amount": fee_eth,
        "total_input_btc": 0.0,
        "total_output_btc": 0.0,
        "fee_btc": 0.0,
        "input_addresses": input_addresses,
        "output_addresses": output_addresses,
        "address_operations": [
            {
                "address": from_address,
                "direction": "out",
                "behavior": "transfer_out",
                "amount": amount_eth,
                "amount_eth": amount_eth,
                "input_amount": amount_eth,
                "output_amount": 0.0,
                "asset": "ETH",
            },
            {
                "address": to_address,
                "direction": "in",
                "behavior": "transfer_in",
                "amount": amount_eth,
                "amount_eth": amount_eth,
                "input_amount": 0.0,
                "output_amount": amount_eth,
                "asset": "ETH",
            },
        ],
        "exchange_hints": _exchange_hints(tx),
        "source_url": f"https://etherscan.io/tx/{tx_hash}",
        "raw": {
            "chain": "ethereum",
            "hash": tx_hash,
            "nonce": tx.get("nonce"),
            "transactionIndex": tx.get("transactionIndex"),
            "gas": tx.get("gas"),
            "gasPrice": tx.get("gasPrice"),
            "input": tx.get("input"),
        },
    }


def _hex_int(value: Any, fallback: int = 0) -> int:
    try:
        if value in (None, ""):
            return fallback
        text = str(value)
        return int(text, 16) if text.startswith("0x") else int(float(text))
    except (TypeError, ValueError):
        return fallback


def _timestamp_iso(value: int) -> str:
    if value <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _exchange_hints(tx: dict[str, Any]) -> list[str]:
    text = json.dumps(tx, ensure_ascii=False).casefold()
    hints = []
    for name in ("coinbase", "binance", "okx", "kraken", "bitfinex", "bitstamp"):
        if name in text:
            hints.append(name)
    return hints
