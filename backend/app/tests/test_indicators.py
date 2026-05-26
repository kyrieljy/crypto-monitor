from __future__ import annotations

from backend.app.services.indicators import BollPoint, detect_boll_break, detect_ma_cross


def test_ma_cross_above_and_below() -> None:
    assert detect_ma_cross(10, 11, 12, 11) == "MA_CROSS_ABOVE"
    assert detect_ma_cross(12, 11, 10, 11) == "MA_CROSS_BELOW"
    assert detect_ma_cross(12, 11, 13, 11) is None


def test_boll_breaks_only_on_fresh_cross() -> None:
    previous = BollPoint("BTCUSDT", 1, middle=100, upper=110, lower=90, close=109)
    current = BollPoint("BTCUSDT", 2, middle=101, upper=111, lower=91, close=112)
    assert detect_boll_break(previous, current) == "BOLL_CROSS_ABOVE_UPPER"

    previous = BollPoint("BTCUSDT", 1, middle=100, upper=110, lower=90, close=91)
    current = BollPoint("BTCUSDT", 2, middle=99, upper=109, lower=89, close=88)
    assert detect_boll_break(previous, current) == "BOLL_CROSS_BELOW_LOWER"

    previous = BollPoint("BTCUSDT", 1, middle=100, upper=110, lower=90, close=112)
    current = BollPoint("BTCUSDT", 2, middle=101, upper=111, lower=91, close=113)
    assert detect_boll_break(previous, current) is None
