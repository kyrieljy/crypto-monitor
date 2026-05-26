import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { formatNumber } from "../lib/format";

interface CoinChartProps {
  symbol: string;
  interval?: string;
  onSourceChange?: (source: { source: string; source_role: string } | null) => void;
}

export function CoinChart({ symbol, interval = "15m", onSourceChange }: CoinChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["klines", symbol, interval],
    queryFn: () => api.klines(symbol, interval),
    refetchInterval: 60_000
  });

  useEffect(() => {
    if (!containerRef.current || !data?.length) return;
    containerRef.current.innerHTML = "";
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 138,
      layout: {
        background: { color: "transparent" },
        textColor: getComputedStyle(document.documentElement).getPropertyValue("--muted"),
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "rgba(148, 163, 184, 0.10)" },
        horzLines: { color: "rgba(148, 163, 184, 0.10)" }
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.16)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.16)", timeVisible: true }
    });
    const series = chart.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444"
    });
    series.setData(data.map((item) => ({ ...item, time: item.time as UTCTimestamp })));
    chart.timeScale().fitContent();
    chartRef.current = chart;
    const resize = () => chart.applyOptions({ width: containerRef.current?.clientWidth ?? 260 });
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  const latest = data?.[data.length - 1];
  const change = latest ? ((latest.close - latest.open) / latest.open) * 100 : null;

  useEffect(() => {
    onSourceChange?.(latest ? { source: latest.source, source_role: latest.source_role } : null);
  }, [latest?.source, latest?.source_role, onSourceChange]);

  return (
    <div className="coin-chart">
      <div className="coin-chart__head">
        <div>
          <strong>{symbol}</strong>
          <span>{interval}</span>
        </div>
        <b className={latest && latest.close >= latest.open ? "up" : "down"}>
          {latest ? `${formatNumber(latest.close, 3)} ${change !== null ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : ""}` : "—"}
        </b>
      </div>
      <div ref={containerRef} className="coin-chart__canvas">
        {isLoading && <span className="empty">加载 {interval} K 线中</span>}
        {isError && <span className="empty">行情暂不可用</span>}
      </div>
    </div>
  );
}
