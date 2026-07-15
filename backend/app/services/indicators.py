from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable

from .market_data import Candle


@dataclass(frozen=True)
class IndicatorPoint:
    symbol: str
    candle_open_time_ms: int
    k: float
    d: float
    j: float


@dataclass(frozen=True)
class BollPoint:
    symbol: str
    candle_open_time_ms: int
    middle: float
    upper: float
    lower: float
    close: float


def calculate_kdj(
    candles: Iterable[Candle],
    period: int,
    k_smoothing: int,
    d_smoothing: int,
) -> list[IndicatorPoint]:
    ordered = sorted(candles, key=lambda item: item.open_time_ms)
    if len(ordered) < period:
        return []

    result: list[IndicatorPoint] = []
    prev_k = 50.0
    prev_d = 50.0
    for index, candle in enumerate(ordered):
        if index + 1 < period:
            continue
        window = ordered[index + 1 - period : index + 1]
        low_price = min(item.low_price for item in window)
        high_price = max(item.high_price for item in window)
        rsv = 50.0 if high_price == low_price else (candle.close_price - low_price) / (high_price - low_price) * 100.0
        current_k = ((k_smoothing - 1) * prev_k + rsv) / k_smoothing
        current_d = ((d_smoothing - 1) * prev_d + current_k) / d_smoothing
        current_j = 3 * current_k - 2 * current_d
        result.append(IndicatorPoint(candle.symbol, candle.open_time_ms, current_k, current_d, current_j))
        prev_k = current_k
        prev_d = current_d
    return result


def moving_average(candles: list[Candle], period: int) -> float:
    window = candles[-period:]
    return sum(candle.close_price for candle in window) / period


def calculate_boll(candles: Iterable[Candle], period: int, stddev_factor: float) -> list[BollPoint]:
    ordered = sorted(candles, key=lambda item: item.open_time_ms)
    if len(ordered) < period:
        return []
    points: list[BollPoint] = []
    for index, candle in enumerate(ordered):
        if index + 1 < period:
            continue
        window = ordered[index + 1 - period : index + 1]
        closes = [item.close_price for item in window]
        middle = sum(closes) / period
        stddev = statistics.pstdev(closes)
        points.append(
            BollPoint(
                symbol=candle.symbol,
                candle_open_time_ms=candle.open_time_ms,
                middle=middle,
                upper=middle + stddev_factor * stddev,
                lower=middle - stddev_factor * stddev,
                close=candle.close_price,
            )
        )
    return points


def detect_kdj_cross(previous: IndicatorPoint, current: IndicatorPoint) -> str | None:
    if previous.j <= previous.k and current.j > current.k:
        return "J_CROSS_ABOVE_K"
    if previous.j >= previous.k and current.j < current.k:
        return "J_CROSS_BELOW_K"
    return None


def detect_ma_cross(previous_fast: float, previous_slow: float, current_fast: float, current_slow: float) -> str | None:
    if previous_fast <= previous_slow and current_fast > current_slow:
        return "MA_CROSS_ABOVE"
    if previous_fast >= previous_slow and current_fast < current_slow:
        return "MA_CROSS_BELOW"
    return None


def detect_boll_middle_ma_cross(previous_middle: float, previous_ma: float, current_middle: float, current_ma: float) -> str | None:
    if previous_middle <= previous_ma and current_middle > current_ma:
        return "BOLL_MIDDLE_CROSS_ABOVE_MA"
    if previous_middle >= previous_ma and current_middle < current_ma:
        return "BOLL_MIDDLE_CROSS_BELOW_MA"
    return None


def detect_boll_break(previous: BollPoint, current: BollPoint) -> str | None:
    if previous.close <= previous.upper and current.close > current.upper:
        return "BOLL_CROSS_ABOVE_UPPER"
    if previous.close >= previous.lower and current.close < current.lower:
        return "BOLL_CROSS_BELOW_LOWER"
    return None
