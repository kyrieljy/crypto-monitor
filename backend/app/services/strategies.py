from __future__ import annotations

import asyncio
import logging
from typing import Any

from .events import EventBus
from .indicators import (
    calculate_boll,
    calculate_kdj,
    detect_boll_break,
    detect_boll_middle_ma_cross,
    detect_kdj_cross,
    detect_ma_cross,
    moving_average,
)
from .market_data import DataSourceRouter
from .store import Store


LOGGER = logging.getLogger("market_monitor.strategies")


SIGNAL_LABELS = {
    "J_CROSS_ABOVE_K": "J 上穿 K",
    "J_CROSS_BELOW_K": "J 下穿 K",
    "MA_CROSS_ABOVE": "快线 MA 上穿慢线 MA",
    "MA_CROSS_BELOW": "快线 MA 下穿慢线 MA",
    "BOLL_CROSS_ABOVE_UPPER": "收盘价上穿 BOLL 上轨",
    "BOLL_CROSS_BELOW_LOWER": "收盘价下穿 BOLL 下轨",
    "BOLL_MIDDLE_CROSS_ABOVE_MA": "BOLL 中轨上穿 MA",
    "BOLL_MIDDLE_CROSS_BELOW_MA": "BOLL 中轨下穿 MA",
}

STRATEGY_ALERT_TITLES = {
    "boll_ma_cross": "BOLL中轨/MA",
}

DASHBOARD_MONITOR_INTERVALS = ("4h", "1h", "15m", "5m")


class TechnicalStrategyRunner:
    def __init__(self, store: Store, market_router: DataSourceRouter, bus: EventBus) -> None:
        self.store = store
        self.market_router = market_router
        self.bus = bus

    async def run_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except Exception:  # noqa: BLE001
                LOGGER.exception("技术策略轮询失败")
            await asyncio.sleep(self._next_sleep())

    def _next_sleep(self) -> int:
        seconds = 10
        for strategy_id in ("kdj", "ma", "boll", "boll_ma_cross"):
            strategy = self.store.get_strategy(strategy_id)
            if strategy and strategy.enabled:
                seconds = min(seconds, int(strategy.config.get("poll_seconds", 10)))
        return max(3, seconds)

    def run_once(self) -> None:
        self._run_kdj()
        self._run_ma()
        self._run_boll()
        self._run_boll_ma_cross()

    def _symbols_for(self, config: dict[str, Any]) -> list[str]:
        configured = [str(item).upper() for item in config.get("symbols", []) if str(item).strip()]
        enabled = set(self.store.enabled_symbols())
        return [symbol for symbol in configured if symbol in enabled] or list(enabled)

    def _notify_symbols_for(self, config: dict[str, Any]) -> set[str]:
        enabled = set(self.store.enabled_symbols())
        if "notify_symbols" in config:
            raw = config.get("notify_symbols")
            configured = raw if isinstance(raw, list) else []
        else:
            raw = config.get("symbols")
            configured = raw if isinstance(raw, list) and raw else list(enabled)
        return {str(item).upper() for item in configured if str(item).strip()} & enabled

    def _configured_intervals(self, config: dict[str, Any], default: list[str]) -> list[str]:
        raw = config.get("intervals")
        if raw is None and config.get("interval"):
            raw = [config.get("interval")]
        intervals = [str(item) for item in (raw or default) if str(item).strip()]
        return intervals or default

    def _intervals_to_monitor(self, notify_intervals: list[str]) -> list[str]:
        result: list[str] = []
        for interval in [*DASHBOARD_MONITOR_INTERVALS, *notify_intervals]:
            if interval not in result:
                result.append(interval)
        return result

    def _run_kdj(self) -> None:
        strategy = self.store.get_strategy("kdj")
        if strategy is None or not strategy.enabled:
            return
        config = strategy.config
        symbols = self._symbols_for(config)
        notify_symbols = self._notify_symbols_for(config)
        notify_intervals = self._configured_intervals(config, ["5m", "15m", "1h"])
        notify_interval_set = set(notify_intervals)
        intervals = self._intervals_to_monitor(notify_intervals)
        for symbol in symbols:
            for interval in intervals:
                try:
                    candles, source, source_role = self.market_router.fetch_klines(
                        symbol,
                        interval,
                        int(config.get("candle_limit", 200)),
                        str(config.get("data_source", "okx_only")),
                    )
                    self.store.record_source_success(source, source)
                    target = candles if config.get("alert_on_live_candle") else candles[:-1]
                    if len(target) < int(config.get("period", 26)) + 1:
                        continue
                    points = calculate_kdj(
                        target,
                        int(config.get("period", 26)),
                        int(config.get("k_smoothing", 20)),
                        int(config.get("d_smoothing", 9)),
                    )
                    if len(points) < 2:
                        continue
                    signal = detect_kdj_cross(points[-2], points[-1])
                    if signal:
                        self._create_technical_alert(
                            strategy_id="kdj",
                            symbol=symbol,
                            interval=interval,
                            signal=signal,
                            candle_open_time_ms=points[-1].candle_open_time_ms,
                            close_price=target[-1].close_price,
                            source=source,
                            source_role=source_role,
                            notify=interval in notify_interval_set and symbol in notify_symbols,
                            detail={"K": points[-1].k, "D": points[-1].d, "J": points[-1].j},
                        )
                except Exception as exc:  # noqa: BLE001
                    self.store.record_source_error("market_data", "行情数据", str(exc))
                    LOGGER.exception("KDJ 检查失败 symbol=%s interval=%s", symbol, interval)

    def _run_ma(self) -> None:
        strategy = self.store.get_strategy("ma")
        if strategy is None or not strategy.enabled:
            return
        config = strategy.config
        symbols = self._symbols_for(config)
        notify_symbols = self._notify_symbols_for(config)
        notify_intervals = self._configured_intervals(config, [str(config.get("interval", "1d"))])
        notify_interval_set = set(notify_intervals)
        intervals = self._intervals_to_monitor(notify_intervals)
        fast_period = int(config.get("fast_period", 25))
        slow_period = int(config.get("slow_period", 99))
        for symbol in symbols:
            for interval in intervals:
                try:
                    candles, source, source_role = self.market_router.fetch_klines(
                        symbol,
                        interval,
                        int(config.get("candle_limit", 200)),
                        str(config.get("data_source", "okx_only")),
                    )
                    self.store.record_source_success(source, source)
                    target = candles if config.get("alert_on_live_candle") else candles[:-1]
                    if len(target) < slow_period + 1:
                        continue
                    previous = target[:-1]
                    previous_fast = moving_average(previous, fast_period)
                    previous_slow = moving_average(previous, slow_period)
                    current_fast = moving_average(target, fast_period)
                    current_slow = moving_average(target, slow_period)
                    signal = detect_ma_cross(previous_fast, previous_slow, current_fast, current_slow)
                    if signal:
                        self._create_technical_alert(
                            strategy_id="ma",
                            symbol=symbol,
                            interval=interval,
                            signal=signal,
                            candle_open_time_ms=target[-1].open_time_ms,
                            close_price=target[-1].close_price,
                            source=source,
                            source_role=source_role,
                            notify=interval in notify_interval_set and symbol in notify_symbols,
                            detail={"fast_ma": current_fast, "slow_ma": current_slow},
                        )
                except Exception as exc:  # noqa: BLE001
                    self.store.record_source_error("market_data", "行情数据", str(exc))
                    LOGGER.exception("MA 检查失败 symbol=%s interval=%s", symbol, interval)

    def _run_boll(self) -> None:
        strategy = self.store.get_strategy("boll")
        if strategy is None or not strategy.enabled:
            return
        config = strategy.config
        symbols = self._symbols_for(config)
        notify_symbols = self._notify_symbols_for(config)
        notify_intervals = self._configured_intervals(config, ["1h", "4h"])
        notify_interval_set = set(notify_intervals)
        intervals = self._intervals_to_monitor(notify_intervals)
        period = int(config.get("period", 20))
        stddev = float(config.get("stddev", 2.0))
        for symbol in symbols:
            for interval in intervals:
                try:
                    candles, source, source_role = self.market_router.fetch_klines(
                        symbol,
                        interval,
                        int(config.get("candle_limit", 200)),
                        str(config.get("data_source", "okx_only")),
                    )
                    self.store.record_source_success(source, source)
                    target = candles if config.get("alert_on_live_candle") else candles[:-1]
                    if len(target) < period + 1:
                        continue
                    points = calculate_boll(target, period, stddev)
                    if len(points) < 2:
                        continue
                    signal = detect_boll_break(points[-2], points[-1])
                    if signal:
                        self._create_technical_alert(
                            strategy_id="boll",
                            symbol=symbol,
                            interval=interval,
                            signal=signal,
                            candle_open_time_ms=points[-1].candle_open_time_ms,
                            close_price=points[-1].close,
                            source=source,
                            source_role=source_role,
                            notify=interval in notify_interval_set and symbol in notify_symbols,
                            detail={
                                "middle": points[-1].middle,
                                "upper": points[-1].upper,
                                "lower": points[-1].lower,
                            },
                        )
                except Exception as exc:  # noqa: BLE001
                    self.store.record_source_error("market_data", "行情数据", str(exc))
                    LOGGER.exception("BOLL 检查失败 symbol=%s interval=%s", symbol, interval)

    def _run_boll_ma_cross(self) -> None:
        strategy = self.store.get_strategy("boll_ma_cross")
        if strategy is None or not strategy.enabled:
            return
        config = strategy.config
        symbols = self._symbols_for(config)
        notify_symbols = self._notify_symbols_for(config)
        notify_intervals = self._configured_intervals(config, ["1h", "4h"])
        notify_interval_set = set(notify_intervals)
        intervals = self._intervals_to_monitor(notify_intervals)
        boll_period = max(1, int(config.get("boll_period", 20)))
        ma_period = max(1, int(config.get("ma_period", 99)))
        minimum_candles = max(boll_period, ma_period) + 1
        for symbol in symbols:
            for interval in intervals:
                try:
                    candles, source, source_role = self.market_router.fetch_klines(
                        symbol,
                        interval,
                        int(config.get("candle_limit", 200)),
                        str(config.get("data_source", "okx_only")),
                    )
                    self.store.record_source_success(source, source)
                    target = candles if config.get("alert_on_live_candle") else candles[:-1]
                    if len(target) < minimum_candles:
                        continue
                    previous = target[:-1]
                    previous_middle = moving_average(previous, boll_period)
                    current_middle = moving_average(target, boll_period)
                    previous_ma = moving_average(previous, ma_period)
                    current_ma = moving_average(target, ma_period)
                    signal = detect_boll_middle_ma_cross(previous_middle, previous_ma, current_middle, current_ma)
                    if signal:
                        self._create_technical_alert(
                            strategy_id="boll_ma_cross",
                            symbol=symbol,
                            interval=interval,
                            signal=signal,
                            candle_open_time_ms=target[-1].open_time_ms,
                            close_price=target[-1].close_price,
                            source=source,
                            source_role=source_role,
                            notify=interval in notify_interval_set and symbol in notify_symbols,
                            detail={
                                "boll_middle": current_middle,
                                "ma": current_ma,
                                "boll_period": boll_period,
                                "ma_period": ma_period,
                            },
                        )
                except Exception as exc:  # noqa: BLE001
                    self.store.record_source_error("market_data", "行情数据", str(exc))
                    LOGGER.exception("BOLL中轨/MA 检查失败 symbol=%s interval=%s", symbol, interval)

    def _create_technical_alert(
        self,
        *,
        strategy_id: str,
        symbol: str,
        interval: str,
        signal: str,
        candle_open_time_ms: int,
        close_price: float,
        source: str,
        source_role: str,
        notify: bool,
        detail: dict[str, Any],
    ) -> None:
        label = SIGNAL_LABELS.get(signal, signal)
        if strategy_id == "boll_ma_cross":
            ma_period = int(detail.get("ma_period", 99))
            direction = "上穿" if signal == "BOLL_MIDDLE_CROSS_ABOVE_MA" else "下穿"
            label = f"BOLL 中轨{direction} MA{ma_period}"
        title = STRATEGY_ALERT_TITLES.get(strategy_id, strategy_id.upper())
        message = f"【{title} 预警】{symbol} {interval} {label}，收盘价 {close_price:.4f}"
        dedupe_key = f"{strategy_id}:{symbol}:{interval}:{signal}:{candle_open_time_ms}"
        inserted_id = self.store.create_alert(
            strategy_id=strategy_id,
            symbol=symbol,
            interval=interval,
            signal=signal,
            severity="warning",
            message=message,
            detail=detail,
            candle_open_time_ms=candle_open_time_ms,
            close_price=close_price,
            source=source,
            source_role=source_role,
            dedupe_key=dedupe_key,
            suppress_notification=not notify,
        )
        if inserted_id:
            self.bus.publish_threadsafe("alert", {"id": inserted_id, "message": message})
