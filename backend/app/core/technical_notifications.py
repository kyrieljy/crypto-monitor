from __future__ import annotations

from collections.abc import Iterable
from typing import Any


TECHNICAL_STRATEGY_IDS = ("kdj", "ma", "boll", "boll_ma_cross")
TECHNICAL_NOTIFICATION_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")

DEFAULT_NOTIFICATION_INTERVALS: dict[str, tuple[str, ...]] = {
    "kdj": ("5m", "15m", "1h"),
    "ma": ("1d",),
    "boll": ("1h", "4h"),
    "boll_ma_cross": ("1h", "4h"),
}


def _normalized_symbols(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip().upper() for value in values if str(value).strip()))


def legacy_notification_intervals(strategy_id: str, config: dict[str, Any]) -> list[str]:
    raw = config.get("intervals")
    if not raw and config.get("interval"):
        raw = [config.get("interval")]
    defaults = DEFAULT_NOTIFICATION_INTERVALS.get(strategy_id, ())
    values = raw if isinstance(raw, list) and raw else defaults
    return [interval for interval in TECHNICAL_NOTIFICATION_INTERVALS if interval in {str(value) for value in values}]


def legacy_notification_symbols(config: dict[str, Any], fallback_symbols: Iterable[str]) -> list[str]:
    if "notify_symbols" in config:
        return _normalized_symbols(config.get("notify_symbols"))
    configured = _normalized_symbols(config.get("symbols"))
    if configured:
        return configured
    return list(dict.fromkeys(str(symbol).strip().upper() for symbol in fallback_symbols if str(symbol).strip()))


def notification_matrix(
    strategy_id: str,
    config: dict[str, Any],
    fallback_symbols: Iterable[str],
    *,
    enabled_symbols: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """Return the authoritative symbol/interval rules, with legacy fallback."""
    if "notify_intervals_by_symbol" in config:
        raw_matrix = config.get("notify_intervals_by_symbol")
        if not isinstance(raw_matrix, dict):
            raw_matrix = {}
        matrix: dict[str, list[str]] = {}
        for raw_symbol, raw_intervals in raw_matrix.items():
            symbol = str(raw_symbol).strip().upper()
            if not symbol or not isinstance(raw_intervals, list):
                continue
            selected = {str(value) for value in raw_intervals}
            intervals = [interval for interval in TECHNICAL_NOTIFICATION_INTERVALS if interval in selected]
            if intervals:
                matrix[symbol] = intervals
    else:
        intervals = legacy_notification_intervals(strategy_id, config)
        matrix = {
            symbol: list(intervals)
            for symbol in legacy_notification_symbols(config, fallback_symbols)
            if intervals
        }

    if enabled_symbols is None:
        return matrix
    enabled = {str(symbol).strip().upper() for symbol in enabled_symbols if str(symbol).strip()}
    return {symbol: intervals for symbol, intervals in matrix.items() if symbol in enabled}


def notification_intervals_for_symbol(
    strategy_id: str,
    config: dict[str, Any],
    symbol: str,
    enabled_symbols: Iterable[str],
) -> set[str]:
    matrix = notification_matrix(
        strategy_id,
        config,
        enabled_symbols,
        enabled_symbols=enabled_symbols,
    )
    return set(matrix.get(str(symbol).upper(), []))


def technical_notification_enabled(
    strategy_id: str,
    config: dict[str, Any],
    symbol: str,
    interval: str,
    enabled_symbols: Iterable[str],
) -> bool:
    if strategy_id not in TECHNICAL_STRATEGY_IDS:
        return True
    return interval in notification_intervals_for_symbol(strategy_id, config, symbol, enabled_symbols)
