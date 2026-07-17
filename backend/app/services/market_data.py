from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class Candle:
    symbol: str
    open_time_ms: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    source: str


class DataSourceError(Exception):
    pass


class MarketDataSource(Protocol):
    name: str

    def fetch_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        raise NotImplementedError


class BinanceFuturesDataSource:
    name = "binance_futures"

    def __init__(self, base_url: str = "https://fapi.binance.com", timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        import requests

        try:
            response = requests.get(
                f"{self.base_url}/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise DataSourceError(f"Binance request failed: {exc}") from exc

        return [
            Candle(
                symbol=symbol,
                open_time_ms=int(row[0]),
                open_price=float(row[1]),
                high_price=float(row[2]),
                low_price=float(row[3]),
                close_price=float(row[4]),
                volume=float(row[5]),
                source=self.name,
            )
            for row in payload
        ]


OKX_SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "SOLUSDT": "SOL-USDT-SWAP",
    "BNBUSDT": "BNB-USDT-SWAP",
    "ZECUSDT": "ZEC-USDT-SWAP",
}


class OkxSwapDataSource:
    name = "okx_swap"

    def __init__(self, base_url: str = "https://www.okx.com", timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_klines(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        import requests

        inst_id = OKX_SYMBOL_MAP.get(symbol)
        if not inst_id:
            raise DataSourceError(f"Unsupported symbol for OKX: {symbol}")
        rows = []
        seen_timestamps: set[str] = set()
        cursor: str | None = None
        requested = min(max(limit, 1), 1000)

        while len(rows) < requested:
            batch_limit = min(100, requested - len(rows))
            params = {"instId": inst_id, "bar": self._map_interval(interval), "limit": batch_limit}
            if cursor:
                params["after"] = cursor
            try:
                response = requests.get(
                    f"{self.base_url}/api/v5/market/history-candles",
                    params=params,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException as exc:
                raise DataSourceError(f"OKX request failed: {exc}") from exc

            if str(payload.get("code", "0")) != "0":
                raise DataSourceError(f"OKX returned error: {payload}")
            batch = payload.get("data")
            if batch is None:
                raise DataSourceError(f"OKX returned unexpected payload: {payload}")
            if not batch:
                break

            for row in batch:
                timestamp = str(row[0])
                if timestamp in seen_timestamps:
                    continue
                rows.append(row)
                seen_timestamps.add(timestamp)
                if len(rows) >= requested:
                    break

            next_cursor = str(batch[-1][0])
            if next_cursor == cursor or len(batch) < batch_limit:
                break
            cursor = next_cursor

        return [
            Candle(
                symbol=symbol,
                open_time_ms=int(row[0]),
                open_price=float(row[1]),
                high_price=float(row[2]),
                low_price=float(row[3]),
                close_price=float(row[4]),
                volume=float(row[5]),
                source=self.name,
            )
            for row in reversed(rows[:requested])
        ]

    @staticmethod
    def _map_interval(interval: str) -> str:
        interval_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1H",
            "4h": "4H",
            "1d": "1D",
        }
        if interval not in interval_map:
            raise DataSourceError(f"Unsupported OKX interval: {interval}")
        return interval_map[interval]


class DataSourceRouter:
    def __init__(self, timeout_seconds: int = 20) -> None:
        self.binance = BinanceFuturesDataSource(timeout_seconds=timeout_seconds)
        self.okx = OkxSwapDataSource(timeout_seconds=timeout_seconds)

    def fetch_klines(self, symbol: str, interval: str, limit: int, preference: str = "okx_only") -> tuple[list[Candle], str, str]:
        if preference == "binance_only":
            return self.binance.fetch_klines(symbol, interval, limit), self.binance.name, "PRIMARY"
        if preference == "binance_then_okx":
            try:
                return self.binance.fetch_klines(symbol, interval, limit), self.binance.name, "PRIMARY"
            except DataSourceError:
                return self.okx.fetch_klines(symbol, interval, limit), self.okx.name, "BACKUP"
        if preference == "okx_then_binance":
            try:
                return self.okx.fetch_klines(symbol, interval, limit), self.okx.name, "PRIMARY"
            except DataSourceError:
                return self.binance.fetch_klines(symbol, interval, limit), self.binance.name, "BACKUP"
        return self.okx.fetch_klines(symbol, interval, limit), self.okx.name, "PRIMARY"
