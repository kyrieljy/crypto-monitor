import { useEffect, useMemo, useRef } from "react";
import { createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { formatNumber } from "../lib/format";
import type { Kline } from "../types/api";

interface IndicatorSettings {
  maShortPeriod: number;
  maFastPeriod: number;
  maSlowPeriod: number;
  bollPeriod: number;
  bollStddev: number;
  kdjPeriod: number;
  kdjKSmoothing: number;
  kdjDSmoothing: number;
}

type IndicatorMode = "ma" | "boll" | "kdj";

interface CoinChartProps {
  symbol: string;
  interval?: string;
  size?: "compact" | "large";
  showIndicators?: boolean;
  indicatorSettings?: IndicatorSettings;
  indicatorMode?: IndicatorMode;
  onIndicatorModeChange?: (mode: IndicatorMode) => void;
  onSourceChange?: (source: { source: string; source_role: string } | null) => void;
}

const DEFAULT_INDICATORS: IndicatorSettings = {
  maShortPeriod: 7,
  maFastPeriod: 25,
  maSlowPeriod: 99,
  bollPeriod: 20,
  bollStddev: 2,
  kdjPeriod: 26,
  kdjKSmoothing: 20,
  kdjDSmoothing: 9
};

const INDICATOR_ITEMS = [
  { key: "ma", color: "#38bdf8", label: () => "MA", title: "切换显示 MA 均线" },
  { key: "boll", color: "#a78bfa", label: () => "BOLL", title: "切换显示 BOLL 上中下轨" },
  { key: "kdj", color: "#22c55e", label: () => "KDJ", title: "切换显示 KDJ 指标线" }
] as const;

const MA_LINE_COLORS = {
  short: "#34d399",
  fast: "#38bdf8",
  slow: "#f59e0b"
};

const BOLL_LINE_COLORS = {
  upper: "#ef4444",
  middle: "#a78bfa",
  lower: "#22c55e"
};

type CandleSeriesApi = ReturnType<IChartApi["addCandlestickSeries"]>;
type LineSeriesApi = ReturnType<IChartApi["addLineSeries"]>;

export function CoinChart({
  symbol,
  interval = "15m",
  size = "compact",
  showIndicators = false,
  indicatorSettings = DEFAULT_INDICATORS,
  indicatorMode = "ma",
  onIndicatorModeChange,
  onSourceChange
}: CoinChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<CandleSeriesApi | null>(null);
  const indicatorSeriesRef = useRef<LineSeriesApi[]>([]);
  const visibleRangeRef = useRef<{ from: number; to: number } | null>(null);
  const userChangedRangeRef = useRef(false);
  const lastGoodDataRef = useRef<Kline[]>([]);
  const klineLimit = size === "large" ? 600 : 90;
  const dataKey = `${symbol}:${interval}:${klineLimit}`;
  const dataKeyRef = useRef(dataKey);
  if (dataKeyRef.current !== dataKey) {
    dataKeyRef.current = dataKey;
    lastGoodDataRef.current = [];
  }
  const indicatorSettingsKey = useMemo(
    () => [
      indicatorSettings.maShortPeriod,
      indicatorSettings.maFastPeriod,
      indicatorSettings.maSlowPeriod,
      indicatorSettings.bollPeriod,
      indicatorSettings.bollStddev,
      indicatorSettings.kdjPeriod,
      indicatorSettings.kdjKSmoothing,
      indicatorSettings.kdjDSmoothing
    ].join(":"),
    [
      indicatorSettings.maShortPeriod,
      indicatorSettings.maFastPeriod,
      indicatorSettings.maSlowPeriod,
      indicatorSettings.bollPeriod,
      indicatorSettings.bollStddev,
      indicatorSettings.kdjPeriod,
      indicatorSettings.kdjKSmoothing,
      indicatorSettings.kdjDSmoothing
    ]
  );
  const { data, isLoading, isError } = useQuery({
    queryKey: ["klines", symbol, interval, klineLimit],
    queryFn: () => api.klines(symbol, interval, klineLimit),
    refetchInterval: 60_000
  });
  if (data?.length) {
    lastGoodDataRef.current = data;
  }
  const chartRows = data?.length ? data : lastGoodDataRef.current;
  const hasChartRows = chartRows.length > 0;
  const isUsingStaleRows = hasChartRows && !data?.length && !isLoading;

  useEffect(() => {
    visibleRangeRef.current = null;
    userChangedRangeRef.current = false;
  }, [symbol, interval, size]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chartHeight = size === "large" ? 404 : 138;
    const hasKdjPane = showIndicators && indicatorMode === "kdj";
    const chart = createChart(container, {
      width: container.clientWidth,
      height: chartHeight,
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false
      },
      handleScale: {
        axisPressedMouseMove: true,
        mouseWheel: true,
        pinch: true
      },
      layout: {
        background: { color: "transparent" },
        textColor: getComputedStyle(document.documentElement).getPropertyValue("--muted"),
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.10)" },
        horzLines: { color: "rgba(148, 163, 184, 0.10)" }
      },
      rightPriceScale: {
        borderColor: "rgba(148, 163, 184, 0.16)",
        scaleMargins: hasKdjPane ? { top: 0.05, bottom: 0.28 } : { top: 0.08, bottom: 0.08 }
      },
      timeScale: {
        borderColor: "rgba(148, 163, 184, 0.16)",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        fixLeftEdge: false,
        fixRightEdge: false
      }
    });
    const series = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444"
    });
    chartRef.current = chart;
    candleSeriesRef.current = series;
    const rememberRange = (range: { from: number; to: number } | null) => {
      if (range) visibleRangeRef.current = { from: Number(range.from), to: Number(range.to) };
    };
    const markUserRange = () => {
      userChangedRangeRef.current = true;
    };
    const blockPageWheel = (event: WheelEvent) => {
      if (size === "large") event.preventDefault();
      markUserRange();
    };
    const resize = () => chart.applyOptions({ width: container.clientWidth || 260 });
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    chart.timeScale().subscribeVisibleLogicalRangeChange(rememberRange);
    container.addEventListener("wheel", blockPageWheel, { passive: false });
    container.addEventListener("mousedown", markUserRange);
    container.addEventListener("touchstart", markUserRange, { passive: true });
    resizeObserver?.observe(container);
    window.addEventListener("resize", resize);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(rememberRange);
      container.removeEventListener("wheel", blockPageWheel);
      container.removeEventListener("mousedown", markUserRange);
      container.removeEventListener("touchstart", markUserRange);
      resizeObserver?.disconnect();
      window.removeEventListener("resize", resize);
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      indicatorSeriesRef.current = [];
    };
  }, [size, showIndicators, indicatorMode]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!chart || !series || !hasChartRows) return;
    const candleData = chartRows.map((item) => ({ ...item, time: item.time as UTCTimestamp }));
    series.setData(candleData);
    indicatorSeriesRef.current.forEach((line) => chart.removeSeries(line));
    indicatorSeriesRef.current = showIndicators ? addIndicatorLines(chart, chartRows, indicatorSettings, indicatorMode) : [];
    const savedRange = visibleRangeRef.current;
    if (userChangedRangeRef.current && savedRange) {
      chart.timeScale().setVisibleLogicalRange(savedRange);
    } else if (size === "large" && candleData.length > 160) {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, candleData.length - 140),
        to: candleData.length + 4
      });
    } else {
      chart.timeScale().fitContent();
    }
  }, [chartRows, hasChartRows, size, showIndicators, indicatorMode, indicatorSettings, indicatorSettingsKey]);

  const latest = chartRows[chartRows.length - 1];
  const change = latest ? ((latest.close - latest.open) / latest.open) * 100 : null;

  useEffect(() => {
    onSourceChange?.(latest ? { source: latest.source, source_role: latest.source_role } : null);
  }, [latest?.source, latest?.source_role, onSourceChange]);

  return (
    <div className={size === "large" ? "coin-chart large" : "coin-chart"}>
      <div className="coin-chart__head">
        <div>
          <strong>{symbol}</strong>
          <span>{interval}</span>
        </div>
        <b className={latest && latest.close >= latest.open ? "up" : "down"}>
          {latest ? `${formatNumber(latest.close, 3)} ${change !== null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : ""}` : "—"}
        </b>
      </div>
      {showIndicators && (
        <div className="coin-chart__legend">
          {INDICATOR_ITEMS.map((item) => {
            const active = indicatorMode === item.key;
            return (
              <button
                type="button"
                key={item.key}
                className={active ? "active" : ""}
                style={{ color: item.color }}
                aria-pressed={active}
                title={item.title}
                onClick={() => onIndicatorModeChange?.(item.key)}
              >
                {item.label()}
              </button>
            );
          })}
          <div className="coin-chart__legend-lines" aria-label="当前指标线说明">
            {indicatorLegendLines(indicatorMode, indicatorSettings).map((item) => (
              <span
                className="coin-chart__legend-line"
                key={item.label}
                style={{ color: item.color }}
                title={item.title}
              >
                {item.label}
              </span>
            ))}
          </div>
        </div>
      )}
      <div ref={containerRef} className="coin-chart__canvas">
        {isLoading && <span className="empty">加载 {interval} K 线中</span>}
        {isError && <span className="empty">行情暂不可用</span>}
      </div>
    </div>
  );
}

function addIndicatorLines(chart: IChartApi, rows: Kline[], settings: IndicatorSettings, mode: IndicatorMode): LineSeriesApi[] {
  const maShort = sma(rows, settings.maShortPeriod);
  const maFast = sma(rows, settings.maFastPeriod);
  const maSlow = sma(rows, settings.maSlowPeriod);
  const bands = boll(rows, settings.bollPeriod, settings.bollStddev);
  const kdj = calcKdj(rows, settings.kdjPeriod, settings.kdjKSmoothing, settings.kdjDSmoothing);
  const lines: LineSeriesApi[] = [];

  if (mode === "ma") {
    const short = chart.addLineSeries({ color: MA_LINE_COLORS.short, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    short.setData(maShort);
    lines.push(short);
    const fast = chart.addLineSeries({ color: MA_LINE_COLORS.fast, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    fast.setData(maFast);
    lines.push(fast);
    const slow = chart.addLineSeries({ color: MA_LINE_COLORS.slow, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    slow.setData(maSlow);
    lines.push(slow);
  }
  if (mode === "boll") {
    const upper = chart.addLineSeries({ color: BOLL_LINE_COLORS.upper, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    upper.setData(bands.upper);
    lines.push(upper);
    const middle = chart.addLineSeries({ color: BOLL_LINE_COLORS.middle, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    middle.setData(bands.middle);
    lines.push(middle);
    const lower = chart.addLineSeries({ color: BOLL_LINE_COLORS.lower, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    lower.setData(bands.lower);
    lines.push(lower);
  }
  if (mode === "kdj") {
    chart.priceScale("kdj").applyOptions({ visible: false, scaleMargins: { top: 0.76, bottom: 0.02 } });
    const k = chart.addLineSeries({ color: "#22c55e", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceScaleId: "kdj" });
    k.setData(kdj.k);
    lines.push(k);
    const d = chart.addLineSeries({ color: "#60a5fa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceScaleId: "kdj" });
    d.setData(kdj.d);
    lines.push(d);
    const j = chart.addLineSeries({ color: "#f87171", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, priceScaleId: "kdj" });
    j.setData(kdj.j);
    lines.push(j);
  }
  return lines;
}

function indicatorLegendLines(mode: IndicatorMode, settings: IndicatorSettings) {
  if (mode === "ma") {
    return [
      { label: `MA${settings.maShortPeriod}`, color: MA_LINE_COLORS.short, title: `MA${settings.maShortPeriod}: 短期均线` },
      { label: `MA${settings.maFastPeriod}`, color: MA_LINE_COLORS.fast, title: `MA${settings.maFastPeriod}: 快线均线` },
      { label: `MA${settings.maSlowPeriod}`, color: MA_LINE_COLORS.slow, title: `MA${settings.maSlowPeriod}: 慢线均线` }
    ];
  }
  if (mode === "boll") {
    return [
      { label: "上轨", color: BOLL_LINE_COLORS.upper, title: "BOLL 上轨" },
      { label: "中轨", color: BOLL_LINE_COLORS.middle, title: "BOLL 中轨" },
      { label: "下轨", color: BOLL_LINE_COLORS.lower, title: "BOLL 下轨" }
    ];
  }
  return [
    { label: "K", color: "#22c55e", title: "KDJ K 线" },
    { label: "D", color: "#60a5fa", title: "KDJ D 线" },
    { label: "J", color: "#f87171", title: "KDJ J 线" }
  ];
}

function sma(rows: Kline[], period: number) {
  const result: Array<{ time: UTCTimestamp; value: number }> = [];
  let sum = 0;
  rows.forEach((row, index) => {
    sum += row.close;
    if (index >= period) sum -= rows[index - period].close;
    if (index >= period - 1) result.push({ time: row.time as UTCTimestamp, value: sum / period });
  });
  return result;
}

function boll(rows: Kline[], period: number, stddev: number) {
  const upper: Array<{ time: UTCTimestamp; value: number }> = [];
  const middle: Array<{ time: UTCTimestamp; value: number }> = [];
  const lower: Array<{ time: UTCTimestamp; value: number }> = [];
  rows.forEach((row, index) => {
    if (index < period - 1) return;
    const windowRows = rows.slice(index - period + 1, index + 1);
    const avg = windowRows.reduce((sum, item) => sum + item.close, 0) / period;
    const variance = windowRows.reduce((sum, item) => sum + (item.close - avg) ** 2, 0) / period;
    const band = Math.sqrt(variance) * stddev;
    const time = row.time as UTCTimestamp;
    middle.push({ time, value: avg });
    upper.push({ time, value: avg + band });
    lower.push({ time, value: avg - band });
  });
  return { upper, middle, lower };
}

function calcKdj(rows: Kline[], period: number, kSmoothing: number, dSmoothing: number) {
  const k: Array<{ time: UTCTimestamp; value: number }> = [];
  const d: Array<{ time: UTCTimestamp; value: number }> = [];
  const j: Array<{ time: UTCTimestamp; value: number }> = [];
  let prevK = 50;
  let prevD = 50;
  rows.forEach((row, index) => {
    if (index < period - 1) return;
    const windowRows = rows.slice(index - period + 1, index + 1);
    const high = Math.max(...windowRows.map((item) => item.high));
    const low = Math.min(...windowRows.map((item) => item.low));
    const rsv = high === low ? 50 : ((row.close - low) / (high - low)) * 100;
    const nextK = ((kSmoothing - 1) * prevK + rsv) / kSmoothing;
    const nextD = ((dSmoothing - 1) * prevD + nextK) / dSmoothing;
    const nextJ = 3 * nextK - 2 * nextD;
    prevK = nextK;
    prevD = nextD;
    const time = row.time as UTCTimestamp;
    k.push({ time, value: nextK });
    d.push({ time, value: nextD });
    j.push({ time, value: nextJ });
  });
  return { k, d, j };
}
