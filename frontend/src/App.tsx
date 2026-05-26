import { useEffect, useMemo, useRef, useState } from "react";
import GridLayout, { WidthProvider, type Layout } from "react-grid-layout";
import { useIsFetching, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Bell,
  ChartCandlestick,
  Database,
  Eye,
  EyeOff,
  LayoutDashboard,
  LogIn,
  LogOut,
  Languages,
  Menu,
  Moon,
  Newspaper,
  PawPrint,
  Plus,
  RefreshCw,
  Save,
  Settings,
  Sun,
  TestTube2,
  Trash2,
  X
} from "lucide-react";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { CoinChart } from "./components/CoinChart";
import { Panel } from "./components/Panel";
import { Switch } from "./components/Switch";
import { api } from "./lib/api";
import { cnDate, formatNumber, strategyLabels } from "./lib/format";
import type {
  AlertEvent,
  DashboardModule,
  NewsEvent,
  NotifierTarget,
  Snapshot,
  SourceHealth,
  StrategyConfig,
  ThemeMode,
  WhaleAddressCandidate,
  WhaleTargetUpsert,
  WhaleTarget
} from "./types/api";

const Grid = WidthProvider(GridLayout);

type ViewMode = "dashboard" | "admin";
type MutableStrategy = StrategyConfig & { config: Record<string, any> };
const FIXED_DASHBOARD_MODULES = new Set(["charts"]);
const COLLAPSED_ALERT_GROUP_HEIGHT = 184;

const INTERVAL_OPTIONS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const DASHBOARD_INTERVAL_OPTIONS = [
  { value: "4h", label: "4小时" },
  { value: "1h", label: "1小时" },
  { value: "15m", label: "15分钟" },
  { value: "5m", label: "5分钟" }
];
const TRUTH_SOURCE_OPTIONS = [
  { value: "rss", label: "RSS 归档" },
  { value: "truthbrush", label: "TruthBrush" },
  { value: "both", label: "双源容灾" }
];
const DATA_SOURCE_OPTIONS = [
  { value: "okx_only", label: "OKX 永续" },
  { value: "okx_then_binance", label: "OKX 优先，币安备用" },
  { value: "binance_then_okx", label: "币安 Futures 优先，OKX 备用" },
  { value: "binance_only", label: "币安 Futures" }
];
const WHALE_TAG_OPTIONS = ["聪明钱", "巨鲸", "KOL", "机构", "做市商", "交易员", "重点关注"];
const STRATEGY_GROUPS = [
  { title: "技术策略", ids: ["boll", "kdj", "ma"] },
  { title: "巨鲸", ids: ["whale"], whaleTargets: true },
  { title: "社媒和新闻", ids: ["trump_social", "whitehouse"] },
  { title: "翻译和清理", ids: ["translation", "cleanup"] }
];
const THEME_STORAGE_KEY = "cryptoMonitorTheme";
const DASHBOARD_LAYOUT_STORAGE_KEY = "cryptoMonitorDashboardLayout";
const INDICATOR_MODE_STORAGE_KEY = "cryptoMonitorIndicatorMode";
type IndicatorMode = "ma" | "boll" | "kdj";

type DashboardControls = {
  chartInterval: string;
  setChartInterval: (interval: string) => void;
  strategyIntervals: Record<string, string>;
  setStrategyInterval: (strategyId: string, interval: string) => void;
  refreshSnapshot: () => void;
  refreshCharts: () => void;
  snapshotUpdatedAt: number;
  chartUpdatedAt: number;
  snapshotFetching: boolean;
  chartFetching: boolean;
  activeChartSources: Record<string, { source: string; source_role: string }>;
  setActiveChartSource: (symbol: string, value: { source: string; source_role: string } | null) => void;
  indicatorMode: IndicatorMode;
  setIndicatorMode: (mode: IndicatorMode) => void;
  translateNews: (ids: number[]) => void;
  newsTranslating: boolean;
  canTranslateNews: boolean;
};

type IndicatorSettings = {
  maShortPeriod: number;
  maFastPeriod: number;
  maSlowPeriod: number;
  bollPeriod: number;
  bollStddev: number;
  kdjPeriod: number;
  kdjKSmoothing: number;
  kdjDSmoothing: number;
};

function readThemePreference(): ThemeMode {
  return localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
}

function readDashboardLayoutPreference(): Array<{ i: string; x: number; y: number; w: number; h: number }> {
  try {
    const parsed = JSON.parse(localStorage.getItem(DASHBOARD_LAYOUT_STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.i === "string")
      .map((item) => ({
        i: item.i,
        x: Number(item.x) || 0,
        y: Number(item.y) || 0,
        w: Number(item.w) || 1,
        h: Number(item.h) || 1
      }));
  } catch {
    return [];
  }
}

function readIndicatorModePreference(): IndicatorMode {
  const mode = localStorage.getItem(INDICATOR_MODE_STORAGE_KEY);
  if (mode === "boll" || mode === "kdj" || mode === "ma") return mode;
  return "ma";
}

function App() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<ViewMode>("dashboard");
  const [theme, setThemeState] = useState<ThemeMode>(() => readThemePreference());
  const [localLayout, setLocalLayout] = useState<Array<{ i: string; x: number; y: number; w: number; h: number }>>(() => readDashboardLayoutPreference());
  const [selectedWhaleId, setSelectedWhaleId] = useState<string | null>(null);
  const dashboardScrollBeforeWhaleRef = useRef<number | null>(null);
  const snapshotQuery = useQuery({ queryKey: ["snapshot"], queryFn: api.snapshot, refetchInterval: 30_000 });
  const { data, isLoading, isError } = snapshotQuery;
  const anyFetching = useIsFetching() > 0;

  const setTheme = (nextTheme: ThemeMode) => {
    setThemeState(nextTheme);
    localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  };

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const stream = new EventSource("/api/stream");
    const refresh = () => queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    stream.addEventListener("alert", refresh);
    stream.addEventListener("news", refresh);
    stream.addEventListener("health", refresh);
    return () => stream.close();
  }, [queryClient]);

  useEffect(() => {
    if (view === "dashboard" && selectedWhaleId) {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
  }, [view, selectedWhaleId]);

  useEffect(() => {
    if (view !== "dashboard" || selectedWhaleId || dashboardScrollBeforeWhaleRef.current === null) return;
    const restoreY = dashboardScrollBeforeWhaleRef.current;
    dashboardScrollBeforeWhaleRef.current = null;
    let retryTimer = 0;
    const frame = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: restoreY, left: 0, behavior: "auto" });
        retryTimer = window.setTimeout(() => {
          window.scrollTo({ top: restoreY, left: 0, behavior: "auto" });
        }, 80);
      });
    });
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(retryTimer);
    };
  }, [view, selectedWhaleId]);

  const openWhaleDetail = (id: string) => {
    dashboardScrollBeforeWhaleRef.current = window.scrollY;
    setSelectedWhaleId(id);
  };

  const visibleLayout = useMemo(() => {
    const modules = new Map((data?.modules ?? []).map((item) => [item.id, item]));
    const localById = new Map(localLayout.map((item) => [item.i, item]));
    return (data?.layout.layout ?? [])
      .map((item) => {
        const local = localById.get(item.i);
        return local ? { ...item, x: local.x, y: local.y, w: local.w, h: local.h } : item;
      })
      .filter((item) => modules.get(item.i)?.enabled && modules.get(item.i)?.visible)
      .map((item) => (
        FIXED_DASHBOARD_MODULES.has(item.i)
          ? { ...item, static: true, isDraggable: false, isResizable: false }
          : item
      ));
  }, [data, localLayout]);

  return (
    <Shell
      view={view}
      setView={(next) => {
        setView(next);
        setSelectedWhaleId(null);
      }}
      theme={theme}
      isRefreshing={anyFetching}
      onRefreshAll={() => {
        void snapshotQuery.refetch();
        void queryClient.invalidateQueries({ queryKey: ["klines"] });
        void queryClient.invalidateQueries({ queryKey: ["whales"] });
      }}
      setTheme={setTheme}
    >
      {isLoading && <div className="boot">正在加载监控系统…</div>}
      {(isError || !data) && !isLoading && <div className="boot">后端暂不可用，请确认服务已启动。</div>}
      {data && view === "dashboard" && !selectedWhaleId && (
        <Dashboard
          data={data}
          layout={visibleLayout}
          onWhaleSelect={openWhaleDetail}
          snapshotUpdatedAt={snapshotQuery.dataUpdatedAt}
          snapshotFetching={snapshotQuery.isFetching}
          refreshSnapshot={() => { void snapshotQuery.refetch(); }}
          onLayoutChange={(layout) => {
            const nextLayout = layout.map(({ i, x, y, w, h }) => ({ i, x, y, w, h }));
            setLocalLayout(nextLayout);
            localStorage.setItem(DASHBOARD_LAYOUT_STORAGE_KEY, JSON.stringify(nextLayout));
          }}
        />
      )}
      {data && view === "dashboard" && selectedWhaleId && <WhaleDetailPage targetId={selectedWhaleId} onBack={() => setSelectedWhaleId(null)} />}
      {data && view === "admin" && <Admin data={data} />}
    </Shell>
  );
}

function Shell({
  view,
  setView,
  theme,
  isRefreshing,
  onRefreshAll,
  setTheme,
  children
}: {
  view: ViewMode;
  setView: (view: ViewMode) => void;
  theme: ThemeMode;
  isRefreshing: boolean;
  onRefreshAll: () => void;
  setTheme: (theme: ThemeMode) => void;
  children: React.ReactNode;
}) {
  const [sideCollapsed, setSideCollapsed] = useState(() => localStorage.getItem("sideCollapsed") === "1");

  useEffect(() => {
    localStorage.setItem("sideCollapsed", sideCollapsed ? "1" : "0");
  }, [sideCollapsed]);

  return (
    <div className={sideCollapsed ? "app-shell side-collapsed" : "app-shell"}>
      <aside className="side">
        <div className="brand">
          <span className="brand__mark"><Activity size={20} /></span>
          <div>
            <strong className="side-label">Crypto Monitor</strong>
          </div>
        </div>
        <button
          className="side-toggle"
          type="button"
          title={sideCollapsed ? "展开菜单" : "收起菜单"}
          aria-label={sideCollapsed ? "展开菜单" : "收起菜单"}
          aria-pressed={sideCollapsed}
          onClick={() => setSideCollapsed((current) => !current)}
        >
          <Menu size={17} />
        </button>
        <nav className="nav">
          <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}><LayoutDashboard size={18} /><span className="nav-label">看板</span></button>
          <button className={view === "admin" ? "active" : ""} onClick={() => setView("admin")}><Settings size={18} /><span className="nav-label">后台</span></button>
        </nav>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <h1>{view === "dashboard" ? "实时监控看板" : "后台管理"}</h1>
          </div>
          <div className="topbar__actions">
            <button className="icon-button" title="刷新全部" onClick={onRefreshAll} disabled={isRefreshing}><RefreshCw className={isRefreshing ? "spin" : ""} size={18} /></button>
            <button className="icon-button" title="深色主题" onClick={() => setTheme("dark")} aria-pressed={theme === "dark"}><Moon size={18} /></button>
            <button className="icon-button" title="浅色主题" onClick={() => setTheme("light")} aria-pressed={theme === "light"}><Sun size={18} /></button>
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}

function Dashboard({
  data,
  layout,
  onLayoutChange,
  onWhaleSelect,
  snapshotUpdatedAt,
  snapshotFetching,
  refreshSnapshot
}: {
  data: Snapshot;
  layout: Layout[];
  onLayoutChange: (layout: Layout[]) => void;
  onWhaleSelect: (id: string) => void;
  snapshotUpdatedAt: number;
  snapshotFetching: boolean;
  refreshSnapshot: () => void;
}) {
  const queryClient = useQueryClient();
  const chartFetching = useIsFetching({ queryKey: ["klines"] }) > 0;
  const canTranslateNews = Boolean(localStorage.getItem("adminToken"));
  const translateNews = useMutation({
    mutationFn: api.translateNews,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    }
  });
  const modules = new Map(data.modules.map((item) => [item.id, item]));
  const fixedLayout = layout.filter((item) => FIXED_DASHBOARD_MODULES.has(item.i));
  const gridSourceLayout = layout.filter((item) => !FIXED_DASHBOARD_MODULES.has(item.i));
  const gridOffsetY = gridSourceLayout.length > 0 ? Math.min(...gridSourceLayout.map((item) => Number(item.y || 0))) : 0;
  const gridLayout = gridSourceLayout.map((item) => ({ ...item, y: Number(item.y || 0) - gridOffsetY }));
  const handleGridLayoutChange = (nextLayout: Layout[]) => {
    onLayoutChange([
      ...fixedLayout,
      ...nextLayout.map((item) => ({ ...item, y: Number(item.y || 0) + gridOffsetY }))
    ]);
  };
  const [chartInterval, setChartInterval] = useState(() => localStorage.getItem("dashboardChartInterval") || "15m");
  const [strategyIntervals, setStrategyIntervals] = useState<Record<string, string>>(() => {
    try {
      return { kdj: "15m", ma: "15m", boll: "15m", ...JSON.parse(localStorage.getItem("dashboardStrategyIntervals") || "{}") };
    } catch {
      return { kdj: "15m", ma: "15m", boll: "15m" };
    }
  });
  useEffect(() => {
    localStorage.setItem("dashboardChartInterval", chartInterval);
  }, [chartInterval]);
  useEffect(() => {
    localStorage.setItem("dashboardStrategyIntervals", JSON.stringify(strategyIntervals));
  }, [strategyIntervals]);
  const [activeChartSources, setActiveChartSources] = useState<Record<string, { source: string; source_role: string }>>({});
  const [indicatorMode, setIndicatorMode] = useState<IndicatorMode>(() => readIndicatorModePreference());
  useEffect(() => {
    localStorage.setItem(INDICATOR_MODE_STORAGE_KEY, indicatorMode);
  }, [indicatorMode]);
  const controls: DashboardControls = {
    chartInterval,
    setChartInterval,
    strategyIntervals,
    setStrategyInterval: (strategyId, interval) => setStrategyIntervals((current) => ({ ...current, [strategyId]: interval })),
    refreshSnapshot,
    refreshCharts: () => { void queryClient.invalidateQueries({ queryKey: ["klines"] }); },
    snapshotUpdatedAt,
    chartUpdatedAt: latestQueryUpdatedAt(queryClient, ["klines"]),
    snapshotFetching,
    chartFetching,
    activeChartSources,
    setActiveChartSource: (symbol, value) => {
      setActiveChartSources((current) => {
        if (!value) {
          const next = { ...current };
          delete next[symbol];
          return next;
        }
        return { ...current, [symbol]: value };
      });
    },
    indicatorMode,
    setIndicatorMode,
    translateNews: (ids) => translateNews.mutate(ids),
    newsTranslating: translateNews.isPending,
    canTranslateNews
  };
  return (
    <>
      {fixedLayout.map((item) => (
        <div className={`dashboard-fixed-module dashboard-fixed-module--${item.i}`} key={item.i}>
          {renderModule(item.i, modules.get(item.i), data, onWhaleSelect, controls)}
        </div>
      ))}
      <Grid
        className="dashboard-grid"
        layout={gridLayout}
        cols={12}
        rowHeight={38}
        margin={[14, 14]}
        containerPadding={[0, 0]}
        draggableHandle=".drag-handle"
        onDragStop={handleGridLayoutChange}
        onResizeStop={handleGridLayoutChange}
      >
        {gridLayout.map((item) => (
          <div key={item.i}>{renderModule(item.i, modules.get(item.i), data, onWhaleSelect, controls)}</div>
        ))}
      </Grid>
    </>
  );
}

function renderModule(id: string, module: DashboardModule | undefined, data: Snapshot, onWhaleSelect: (id: string) => void, controls: DashboardControls) {
  const title = module?.title ?? id;
  if (id === "charts") {
    const dataSource = String(module?.config?.data_source ?? "okx_then_binance");
    const enabledSymbols = data.symbols.filter((item) => item.enabled).slice(0, 5);
    const ethSymbol = enabledSymbols.find((item) => item.symbol === "ETHUSDT") ?? enabledSymbols[0];
    const secondarySymbols = enabledSymbols.filter((item) => item.symbol !== ethSymbol?.symbol).slice(0, 4);
    const indicatorSettings = indicatorSettingsFromStrategies(data.strategies);
    return (
      <Panel
        title={title}
        dragHandle={false}
        action={
          <ModuleAction
            label={<><ChartCandlestick size={14} /> {intervalLabel(controls.chartInterval)} · {dataSourceLabel(dataSource)} · {activeSourceLabel(controls.activeChartSources)}</>}
            updatedAt={controls.chartUpdatedAt}
            isFetching={controls.chartFetching}
            onRefresh={controls.refreshCharts}
          />
        }
      >
        <div className="chart-toolbar">
          <div className="chart-toolbar__group">
            <span>K 线周期</span>
            <IntervalSegmented value={controls.chartInterval} onChange={controls.setChartInterval} />
          </div>
          <a className="chart-attribution" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a>
          <div className="chart-toolbar__group strategy-cycle-controls">
            <span>策略展示周期</span>
            <StrategyIntervalControl label="KDJ" value={controls.strategyIntervals.kdj ?? "15m"} onChange={(value) => controls.setStrategyInterval("kdj", value)} />
            <StrategyIntervalControl label="MA" value={controls.strategyIntervals.ma ?? "15m"} onChange={(value) => controls.setStrategyInterval("ma", value)} />
            <StrategyIntervalControl label="BOLL" value={controls.strategyIntervals.boll ?? "15m"} onChange={(value) => controls.setStrategyInterval("boll", value)} />
          </div>
        </div>
        <div className="market-monitor-layout">
          {ethSymbol && (
            <section className="eth-feature">
              <div className="eth-feature__chart">
                <CoinChart
                  symbol={ethSymbol.symbol}
                  interval={controls.chartInterval}
                  size="large"
                  showIndicators
                  indicatorSettings={indicatorSettings}
                  indicatorMode={controls.indicatorMode}
                  onIndicatorModeChange={controls.setIndicatorMode}
                  onSourceChange={(value) => controls.setActiveChartSource(ethSymbol.symbol, value)}
                />
              </div>
              <CoinAlertStack
                className="eth-alert-stack"
                alerts={data.alerts.filter((alert) => alert.symbol === ethSymbol.symbol)}
                strategyIntervals={controls.strategyIntervals}
                collapsedHeight={138}
              />
            </section>
          )}
          <div className="coin-wall coin-wall--secondary">
            {secondarySymbols.map((item) => (
              <CoinStrategyCard
                key={item.symbol}
                symbol={item.symbol}
                alerts={data.alerts.filter((alert) => alert.symbol === item.symbol)}
                chartInterval={controls.chartInterval}
                strategyIntervals={controls.strategyIntervals}
                onChartSourceChange={(value) => controls.setActiveChartSource(item.symbol, value)}
              />
            ))}
          </div>
        </div>
      </Panel>
    );
  }
  if (id === "trump_social" || id === "whitehouse") {
    const isWhiteHouse = id === "whitehouse";
    const news = data.news.filter((item) => isWhiteHouse ? item.source_name === "whitehouse_gallery" : item.source_name !== "whitehouse_gallery");
    const visibleNews = news.slice(0, 7);
    const allVisibleNewsChinese = visibleNews.length > 0 && visibleNews.every(isDisplayedNewsChinese);
    const translateTitle = !controls.canTranslateNews
      ? "管理员登录后可翻译"
      : allVisibleNewsChinese
        ? "当前显示的新闻已是中文"
        : controls.newsTranslating
          ? "正在翻译"
          : !visibleNews.length
            ? "暂无可翻译新闻"
            : "翻译当前显示的新闻";
    return (
      <Panel
        title={title}
        action={
          <ModuleAction
            label={<><Newspaper size={14} /> {news.length}</>}
            updatedAt={controls.snapshotUpdatedAt}
            isFetching={controls.snapshotFetching}
            onRefresh={controls.refreshSnapshot}
            extra={
              <button
                className="icon-text-button"
                title={translateTitle}
                disabled={!controls.canTranslateNews || controls.newsTranslating || !visibleNews.length || allVisibleNewsChinese}
                onClick={() => controls.translateNews(visibleNews.map((item) => item.id))}
              >
                <Languages size={14} /> 翻译
              </button>
            }
          />
        }
      >
        <div className="news-list">
          {visibleNews.map((item) => {
            const newsTitle = displayNewsTitle(item);
            const newsSummary = displayNewsSummary(item);
            const media = newsMediaItems(item);
            const card = newsCard(item);
            const newsUrl = newsTargetUrl(item, card);
            const cardTitle = card ? newsCardTitle(card) : "";
            const cardDescription = card ? newsCardDescription(card) : "";
            return (
              <a key={item.id} className="news-row" href={newsUrl} target="_blank" rel="noreferrer">
                {media.length > 0 && (
                  <div className="news-media-grid">
                    {media.slice(0, 2).map((mediaItem, index) => {
                      const imageUrl = newsMediaImageUrl(mediaItem);
                      if (!imageUrl) return null;
                      return (
                        <div className="news-media" key={`${imageUrl}-${index}`}>
                          <img src={imageUrl} alt={newsTitle} loading="lazy" onError={(event) => { event.currentTarget.closest(".news-media")?.classList.add("hidden"); }} />
                          {mediaItem.type === "video" && <span>视频</span>}
                        </div>
                      );
                    })}
                  </div>
                )}
                <strong>{newsTitle}</strong>
                <small>{item.source_name} · {newsKindLabel(item)} · {cnDate(item.published_at_utc)}</small>
                {newsSummary && <p>{newsSummary}</p>}
                {card && (
                  <div className="news-card-preview">
                    {card.image_url && !media.some((mediaItem) => newsMediaImageUrl(mediaItem) === card.image_url) && <img src={card.image_url} alt={cardTitle || newsTitle} loading="lazy" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
                    <div>
                      <b>{cardTitle || "链接预览"}</b>
                      {cardDescription && <span>{cardDescription}</span>}
                    </div>
                  </div>
                )}
              </a>
            );
          })}
          {!news.length && <span className="empty">暂无新闻提醒</span>}
        </div>
      </Panel>
    );
  }
  if (id === "whale") {
    return <WhaleModule title={title} onSelect={onWhaleSelect} />;
  }
  if (id === "alerts") {
    return (
      <Panel
        title={title}
        action={
          <ModuleAction
            label={<><Bell size={14} /> {data.alerts.length}</>}
            updatedAt={controls.snapshotUpdatedAt}
            isFetching={controls.snapshotFetching}
            onRefresh={controls.refreshSnapshot}
          />
        }
      >
        <div className="event-list">
          {data.alerts.slice(0, 10).map((alert) => (
            <div className="event-row" key={alert.id}>
              <span className={`dot ${alert.strategy_id}`} />
              <div>
                <strong>{alert.message}</strong>
                <small>{strategyLabels[alert.strategy_id] ?? alert.strategy_id} · {alert.symbol} · {alert.interval} · {alertSourceLabel(alert)} · {cnDate(alert.created_at)}</small>
              </div>
            </div>
          ))}
          {!data.alerts.length && <span className="empty">暂无策略告警</span>}
        </div>
      </Panel>
    );
  }
  if (id === "health") {
    return (
      <Panel
        title={title}
        action={
          <ModuleAction
            label={<><Database size={14} /> {data.health.length}</>}
            updatedAt={controls.snapshotUpdatedAt}
            isFetching={controls.snapshotFetching}
            onRefresh={controls.refreshSnapshot}
          />
        }
      >
        <div className="health-list">
          {data.health.map((source) => (
            <div className="health-row" key={source.source_name}>
              <span className={`dot ${source.status === "ok" ? "ok" : "error"}`} />
              <div>
                <strong>{source.label}</strong>
                <small title={source.last_error_message || undefined}>{formatHealthStatus(source)}</small>
              </div>
            </div>
          ))}
          {!data.health.length && <span className="empty">暂无数据源状态</span>}
        </div>
      </Panel>
    );
  }
  return (
    <Panel title={title}>
      <span className="empty">该模块已从默认看板隐藏，可在后台重新开启。</span>
    </Panel>
  );
}

function ModuleAction({
  label,
  updatedAt,
  isFetching,
  onRefresh,
  extra,
  labelIsWrapped = true
}: {
  label: React.ReactNode;
  updatedAt: number;
  isFetching: boolean;
  onRefresh: () => void;
  extra?: React.ReactNode;
  labelIsWrapped?: boolean;
}) {
  return (
    <div className="module-action">
      {labelIsWrapped ? <span className="pill">{label}</span> : label}
      <span className="refresh-time">{formatRefreshTime(updatedAt)}</span>
      {extra}
      <button className="icon-button module-refresh" title="刷新本模块" onClick={onRefresh} disabled={isFetching}>
        <RefreshCw className={isFetching ? "spin" : ""} size={14} />
      </button>
    </div>
  );
}

function formatRefreshTime(value: number) {
  if (!value) return "未刷新";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date(value));
}

function latestQueryUpdatedAt(queryClient: ReturnType<typeof useQueryClient>, queryKey: readonly unknown[]) {
  return Math.max(
    0,
    ...queryClient.getQueryCache().findAll({ queryKey }).map((query) => Number(query.state.dataUpdatedAt || 0))
  );
}

function indicatorSettingsFromStrategies(strategies: StrategyConfig[]): IndicatorSettings {
  const kdj = strategies.find((strategy) => strategy.id === "kdj")?.config ?? {};
  const ma = strategies.find((strategy) => strategy.id === "ma")?.config ?? {};
  const boll = strategies.find((strategy) => strategy.id === "boll")?.config ?? {};
  return {
    maShortPeriod: positiveInt(ma.short_period, 7),
    maFastPeriod: positiveInt(ma.fast_period, 25),
    maSlowPeriod: positiveInt(ma.slow_period, 99),
    bollPeriod: positiveInt(boll.period, 20),
    bollStddev: positiveNumber(boll.stddev, 2),
    kdjPeriod: positiveInt(kdj.period, 26),
    kdjKSmoothing: positiveInt(kdj.k_smoothing, 20),
    kdjDSmoothing: positiveInt(kdj.d_smoothing, 9)
  };
}

function positiveInt(value: unknown, fallback: number) {
  const parsed = Math.round(Number(value));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function positiveNumber(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function CoinStrategyCard({
  symbol,
  alerts,
  chartInterval,
  strategyIntervals,
  onChartSourceChange
}: {
  symbol: string;
  alerts: AlertEvent[];
  chartInterval: string;
  strategyIntervals: Record<string, string>;
  onChartSourceChange: (source: { source: string; source_role: string } | null) => void;
}) {
  return (
    <article className="coin-card">
      <CoinChart symbol={symbol} interval={chartInterval} onSourceChange={onChartSourceChange} />
      <CoinAlertStack alerts={alerts} strategyIntervals={strategyIntervals} />
    </article>
  );
}

function CoinAlertStack({
  alerts,
  strategyIntervals,
  collapsedHeight = COLLAPSED_ALERT_GROUP_HEIGHT,
  className = ""
}: {
  alerts: AlertEvent[];
  strategyIntervals: Record<string, string>;
  collapsedHeight?: number;
  className?: string;
}) {
  const groups = [
    ["kdj", "KDJ"],
    ["ma", "MA"],
    ["boll", "BOLL"]
  ] as const;
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [selectedAlert, setSelectedAlert] = useState<AlertEvent | null>(null);
  return (
    <div className={className ? `coin-alerts ${className}` : "coin-alerts"}>
      {groups.map(([strategyId, label]) => {
        const selectedInterval = strategyIntervals[strategyId] ?? "15m";
        const groupKey = `${strategyId}:${selectedInterval}`;
        const allItems = alerts
          .filter((alert) => alert.strategy_id === strategyId && alert.interval === selectedInterval)
          .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
        const canExpand = allItems.length > 3;
        const isExpanded = canExpand && Boolean(expandedGroups[groupKey]);
        const items = allItems.slice(0, isExpanded ? 10 : 3);
        return (
          <CoinAlertGroup
            key={strategyId}
            strategyId={strategyId}
            label={label}
            selectedInterval={selectedInterval}
            items={items}
            canExpand={canExpand}
            isExpanded={isExpanded}
            collapsedHeight={collapsedHeight}
            onToggle={() => setExpandedGroups((current) => ({ ...current, [groupKey]: !isExpanded }))}
            onAlertSelect={setSelectedAlert}
          />
        );
      })}
      {selectedAlert && <AlertDetailDialog alert={selectedAlert} onClose={() => setSelectedAlert(null)} />}
    </div>
  );
}

function CoinAlertGroup({
  strategyId,
  label,
  selectedInterval,
  items,
  canExpand,
  isExpanded,
  collapsedHeight = COLLAPSED_ALERT_GROUP_HEIGHT,
  onToggle,
  onAlertSelect
}: {
  strategyId: string;
  label: string;
  selectedInterval: string;
  items: AlertEvent[];
  canExpand: boolean;
  isExpanded: boolean;
  collapsedHeight?: number;
  onToggle: () => void;
  onAlertSelect: (alert: AlertEvent) => void;
}) {
  const groupRef = useRef<HTMLDivElement | null>(null);
  const [expandedHeight, setExpandedHeight] = useState(collapsedHeight);

  useEffect(() => {
    if (!isExpanded || !groupRef.current) {
      setExpandedHeight(collapsedHeight);
      return;
    }

    const measure = () => {
      if (!groupRef.current) return;
      setExpandedHeight(Math.max(collapsedHeight, Math.ceil(groupRef.current.scrollHeight)));
    };

    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(groupRef.current);
    return () => observer?.disconnect();
  }, [isExpanded, items.length, selectedInterval, collapsedHeight]);

  return (
    <div
      ref={groupRef}
      className={isExpanded ? "coin-alert-group expanded" : "coin-alert-group"}
      style={{ height: `${isExpanded ? expandedHeight : collapsedHeight}px` }}
    >
      <div className="coin-alert-group__title">
        <span className={`dot ${strategyId}`} />
        <strong>{label}</strong>
        <em>{intervalLabel(selectedInterval)}</em>
        <button
          type="button"
          className="coin-alert-toggle"
          disabled={!canExpand}
          onClick={onToggle}
        >
          {isExpanded ? "收起" : "更多"}
        </button>
      </div>
      <div className="coin-alert-list">
        {items.map((alert) => {
          const alertText = formatCoinAlert(alert);
          return (
            <button type="button" className="coin-alert" key={alert.id} onClick={() => onAlertSelect(alert)}>
              <span className="coin-alert__message" title={alertText}>{alertText}</span>
              <small>{cnDate(alert.created_at)}</small>
            </button>
          );
        })}
        {!items.length && <small className="muted">暂无 {intervalLabel(selectedInterval)} 提醒</small>}
      </div>
    </div>
  );
}

function AlertDetailDialog({ alert, onClose }: { alert: AlertEvent; onClose: () => void }) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const metricRows = alertMetricRows(alert);
  const alertText = formatCoinAlert(alert);
  return (
    <div className="alert-detail-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="alert-detail-dialog" role="dialog" aria-modal="true" aria-label={`${alert.symbol} 预警明细`} onMouseDown={(event) => event.stopPropagation()}>
        <button type="button" className="alert-detail-close" title="关闭" aria-label="关闭预警明细" onClick={onClose}><X size={15} /></button>
        <div className="alert-detail-head">
          <span className={`dot ${alert.strategy_id}`} />
          <div>
            <strong>{strategyLabels[alert.strategy_id] ?? alert.strategy_id} 预警明细</strong>
            <small>{alert.symbol} · {intervalLabel(alert.interval)} · {cnDate(alert.created_at)}</small>
          </div>
        </div>
        <div className="alert-detail-summary">{alertText}</div>
        <div className="alert-detail-grid">
          <DetailItem label="信号" value={alertSignalLabel(alert)} />
          <DetailItem label="当前/收盘价" value={formatClosePrice(alert.close_price)} />
          <DetailItem label="数据源" value={alertSourceLabel(alert)} />
          <DetailItem label="提醒时间" value={cnDate(alert.created_at)} />
          {metricRows.map((row) => <DetailItem key={row.label} label={row.label} value={row.value} emphasis={row.emphasis} />)}
        </div>
      </section>
    </div>
  );
}

function DetailItem({ label, value, emphasis = false }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div className={emphasis ? "alert-detail-item emphasis" : "alert-detail-item"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatCoinAlert(alert: AlertEvent) {
  const signal = alertSignalLabel(alert);
  const price = formatClosePrice(alert.close_price);
  return `${alert.interval} ${signal}，收盘价${price}`;
}

function alertSignalLabel(alert: AlertEvent) {
  const signalLabels: Record<string, string> = {
    J_CROSS_ABOVE_K: "J上穿K",
    J_CROSS_BELOW_K: "J下穿K",
    MA_CROSS_ABOVE: "快线上穿慢线",
    MA_CROSS_BELOW: "快线下穿慢线",
    BOLL_CROSS_ABOVE_UPPER: "上穿上轨",
    BOLL_CROSS_BELOW_LOWER: "下穿下轨"
  };
  return signalLabels[alert.signal] ?? alert.signal;
}

function alertMetricRows(alert: AlertEvent): AlertDetailRow[] {
  const detail = alert.detail ?? {};
  if (alert.strategy_id === "kdj") {
    return [
      { label: "K", value: formatIndicatorValue(detail.K), emphasis: true },
      { label: "D", value: formatIndicatorValue(detail.D), emphasis: true },
      { label: "J", value: formatIndicatorValue(detail.J), emphasis: true }
    ];
  }
  if (alert.strategy_id === "ma") {
    return [
      { label: "快线 MA", value: formatIndicatorValue(detail.fast_ma), emphasis: true },
      { label: "慢线 MA", value: formatIndicatorValue(detail.slow_ma), emphasis: true }
    ];
  }
  if (alert.strategy_id === "boll") {
    return [
      { label: "BOLL 上轨", value: formatIndicatorValue(detail.upper), emphasis: true },
      { label: "BOLL 中轨", value: formatIndicatorValue(detail.middle), emphasis: true },
      { label: "BOLL 下轨", value: formatIndicatorValue(detail.lower), emphasis: true }
    ];
  }
  return Object.entries(detail).map(([label, value]) => ({ label, value: formatIndicatorValue(value) }));
}

function formatIndicatorValue(value: unknown) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "--";
  return num.toLocaleString("zh-CN", { maximumFractionDigits: 4, minimumFractionDigits: 4, useGrouping: false });
}

function formatClosePrice(value: unknown) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "--";
  const digits = Math.abs(num) >= 100 ? 2 : 4;
  return num.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: digits, useGrouping: false });
}

function formatHealthStatus(source: SourceHealth) {
  const lastSuccess = cnDate(source.last_success_utc);
  if (source.status === "ok") {
    return `正常 · 最近成功 ${lastSuccess}`;
  }

  const errorText = parseHealthError(source.last_error_message);
  return `异常 · 最近成功 ${lastSuccess} · ${errorText}`;
}

function parseHealthError(message?: string | null) {
  if (!message) return "暂未记录具体错误，请稍后刷新重试。";
  const raw = message.trim();
  const lower = raw.toLowerCase();
  const host = raw.match(/host='([^']+)'/)?.[1] || raw.match(/https?:\/\/([^/\s]+)/i)?.[1];

  if (/errno 2|no such file or directory/i.test(raw)) {
    return "数据源地址配置异常，当前地址可能被当成本地文件读取，请检查 URL。";
  }
  if (/name resolution|getaddrinfo|nodename nor servname|temporary failure in name resolution|dns/i.test(lower)) {
    return `域名解析失败${host ? `（${host}）` : ""}，请检查网络或 DNS。`;
  }
  if (/ssl|ssleoferror|certificate|tls|eof occurred/i.test(raw)) {
    return `HTTPS 连接中断${host ? `（${host}）` : ""}，可能是网络、代理或目标站点临时拒绝。`;
  }
  if (/timed out|timeout|read timed out/i.test(lower)) {
    return `请求超时${host ? `（${host}）` : ""}，数据源响应过慢或网络不稳定。`;
  }
  if (/max retries exceeded/i.test(lower)) {
    return `连接失败并已达到最大重试次数${host ? `（${host}）` : ""}，请检查网络或数据源可用性。`;
  }
  if (/connection reset|connection aborted|remote end closed/i.test(lower)) {
    return `连接被中断${host ? `（${host}）` : ""}，可能是目标站点或代理断开。`;
  }
  if (/http\s*401|status\s*401|unauthorized/i.test(lower)) {
    return "接口鉴权失败，请检查 API Key 或访问权限。";
  }
  if (/http\s*403|status\s*403|forbidden/i.test(lower)) {
    return "接口拒绝访问，可能被限流、区域限制或权限不足。";
  }
  if (/http\s*404|status\s*404|not found/i.test(lower)) {
    return "接口地址不存在，请检查数据源 URL 配置。";
  }
  if (/http\s*429|status\s*429|too many requests/i.test(lower)) {
    return "请求过于频繁，数据源触发限流。";
  }
  if (/json|decode|parse/i.test(lower)) {
    return "数据解析失败，接口返回内容不是预期格式。";
  }
  if (/okx/i.test(lower)) {
    return "OKX 数据请求失败，请检查网络、代理或 OKX 接口状态。";
  }
  if (/binance/i.test(lower)) {
    return "币安数据请求失败，请检查网络线路或币安接口状态。";
  }
  if (/truth|rss|feed/i.test(lower)) {
    return "社媒 RSS 数据读取失败，请检查 RSS 地址和网络连接。";
  }
  if (/whitehouse/i.test(lower)) {
    return "白宫新闻数据读取失败，请检查白宫页面可访问性。";
  }
  return raw.length > 80 ? `${raw.slice(0, 80)}...` : raw;
}

type NewsMediaItem = { type?: string; url?: string; thumbnail_url?: string; source?: string };
type NewsCard = {
  title?: string;
  description?: string;
  translated_title?: string;
  translated_description?: string;
  url?: string;
  image_url?: string;
  media_type?: string;
};
type AlertDetailRow = { label: string; value: string; emphasis?: boolean };

function displayNewsTitle(item: NewsEvent) {
  const title = usableNewsText(item.translated_title) || stripHtml(item.title).trim();
  if (!isGenericNewsTitle(title)) return title;
  const card = newsCard(item);
  if (card && !repostMediaKind(item, card)) {
    const cardTitle = newsCardTitle(card);
    if (cardTitle) return `${newsKindLabel(item)}：${cardTitle}`;
  }
  return `${newsKindLabel(item)}，点击查看`;
}

function displayNewsSummary(item: NewsEvent) {
  const title = displayNewsTitle(item);
  const translatedTitle = usableNewsText(item.translated_title);
  const translatedSummary = usableNewsText(item.translated_summary);
  const card = newsCard(item);
  const translatedCardDescription = card ? usableNewsText(card.translated_description) : "";
  const summary = translatedSummary || translatedCardDescription || stripHtml(item.content).trim();
  const repostMedia = repostMediaKind(item, card);
  if (isLegacyTruthMediaItem(item)) {
    return `${newsKindLabel(item)}，点击查看原帖。`;
  }
  if (repostMedia && (!summary || isNonTextRepostPlaceholder(summary) || isUrlOnlyText(summary))) {
    return `${newsKindLabel(item)}，点击查看原帖。`;
  }
  if (summary && !isBrokenLegacyTruthText(summary) && !isDuplicateNewsText(title, summary) && !isDuplicateNewsText(item.title, item.content) && !isDuplicateNewsText(translatedTitle, translatedSummary)) {
    return summary;
  }
  if (card) {
    const cardDescription = newsCardDescription(card);
    if (cardDescription) return cardDescription;
  }
  if (newsMediaItems(item).length || card) {
    return `${newsKindLabel(item)}，点击查看原帖。`;
  }
  return "";
}

function isDisplayedNewsChinese(item: NewsEvent) {
  return isMostlyChineseText([displayNewsTitle(item), displayNewsSummary(item)].join(" "));
}

function isMostlyChineseText(value: string) {
  const text = stripHtml(value).replace(/\s+/g, "");
  if (!text) return false;
  const chineseCount = text.match(/[\u3400-\u9fff]/g)?.length ?? 0;
  const latinCount = text.match(/[A-Za-z]/g)?.length ?? 0;
  return chineseCount >= 2 && chineseCount >= latinCount * 0.35;
}

function isGenericNewsTitle(value?: string | null) {
  return /^\s*(\[?无标题\]?|\[no title\])/i.test(String(value ?? ""));
}

function usableNewsText(value?: string | null) {
  const text = stripHtml(value || "").trim();
  return isTranslationInstructionText(text) ? "" : text;
}

function isTranslationInstructionText(value: string) {
  if (
    /\u4e0d\u662f\u82f1\u6587\u65b0\u95fb\u6807\u9898\u6216\u6458\u8981/.test(value) ||
    /\u91cd\u65b0\u63d0\u4f9b\u82f1\u6587\u5185\u5bb9/.test(value) ||
    (/\u82f1\u6587\u5185\u5bb9/.test(value) && /\u4ee5\u4fbf\u7ffb\u8bd1/.test(value))
  ) {
    return true;
  }
  return (
    /请提供.*英文新闻标题或摘要/.test(value) ||
    /未包含任何需要翻译/.test(value) ||
    /没有.*可翻译的内容/.test(value) ||
    /无法查看图片/.test(value) ||
    /当前信息仅包含日期/.test(value) ||
    /目前只输入了|目前仅看到日期信息/.test(value)
  );
}

function newsKindLabel(item: NewsEvent) {
  const kind = typeof item.metadata?.content_kind === "string" ? item.metadata.content_kind : "text";
  const repostMedia = repostMediaKind(item);
  if (kind === "repost" && repostMedia === "video") return "转发视频内容";
  if (kind === "repost" && repostMedia === "image") return "转发图片内容";
  if (kind === "text" && isLegacyTruthMediaItem(item)) {
    return "图片内容";
  }
  const labels: Record<string, string> = {
    image: "图片内容",
    video: "视频内容",
    repost: "转发内容",
    link: "链接内容",
    media: "媒体内容",
    text: "文字内容"
  };
  return labels[kind] ?? "文字内容";
}

function repostMediaKind(item: NewsEvent, existingCard?: NewsCard | null) {
  if (item.metadata?.content_kind !== "repost") return "";
  const media = newsMediaItems(item);
  if (media.some((entry) => entry.type === "video")) return "video";
  if (media.length) return "image";
  const card = existingCard ?? newsCard(item);
  if (card?.media_type === "video") return "video";
  if (card?.media_type === "image" || card?.image_url) return "image";
  return "";
}

function newsMediaItems(item: NewsEvent): NewsMediaItem[] {
  const raw = item.metadata?.media;
  const parsed = Array.isArray(raw) ? raw
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object")
    .map((entry) => ({
      type: typeof entry.type === "string" ? entry.type : "image",
      url: typeof entry.url === "string" ? entry.url : "",
      thumbnail_url: typeof entry.thumbnail_url === "string" ? entry.thumbnail_url : "",
      source: typeof entry.source === "string" ? entry.source : ""
    }))
    .filter((entry) => Boolean(newsMediaImageUrl(entry)) && isAllowedNewsMediaUrl(newsMediaImageUrl(entry))) : [];
  return parsed;
}

function newsMediaImageUrl(item: NewsMediaItem) {
  return item.thumbnail_url || item.url || "";
}

function isAllowedNewsMediaUrl(url: string) {
  return !url.toLowerCase().includes("/social_previews/");
}

function newsCard(item: NewsEvent): NewsCard | null {
  const raw = item.metadata?.card;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const card = raw as Record<string, unknown>;
  const result: NewsCard = {
    title: typeof card.title === "string" ? card.title : "",
    description: typeof card.description === "string" ? card.description : "",
    translated_title: typeof card.translated_title === "string" ? card.translated_title : "",
    translated_description: typeof card.translated_description === "string" ? card.translated_description : "",
    url: typeof card.url === "string" ? card.url : "",
    image_url: typeof card.image_url === "string" && isAllowedNewsMediaUrl(card.image_url) ? card.image_url : "",
    media_type: typeof card.media_type === "string" ? card.media_type : ""
  };
  return result.title || result.description || result.url || result.image_url ? result : null;
}

function newsCardTitle(card: NewsCard) {
  return usableNewsText(card.translated_title) || card.title || "";
}

function newsCardDescription(card: NewsCard) {
  return usableNewsText(card.translated_description) || card.description || "";
}

function newsTargetUrl(item: NewsEvent, card?: NewsCard | null) {
  const originalUrl = truthsocialPostUrl(item.metadata?.original_url) || truthsocialPostUrlFromId(item.metadata?.original_id);
  if (item.source_name === "trumps_truth_rss" && originalUrl) return originalUrl;
  if (!originalUrl && item.metadata?.content_kind === "repost" && card?.url) return card.url;
  return item.url;
}

function truthsocialPostUrl(value: unknown) {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "";
  try {
    const url = new URL(raw);
    if (!/(^|\.)truthsocial\.com$/i.test(url.hostname)) return raw;
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length === 2 && parts[0].startsWith("@") && /^\d+$/.test(parts[1])) {
      url.pathname = `/${parts[0]}/posts/${parts[1]}`;
      return url.toString();
    }
    if (parts.length === 4 && parts[0] === "users" && parts[2] === "statuses" && /^\d+$/.test(parts[3])) {
      url.pathname = `/@${parts[1]}/posts/${parts[3]}`;
      return url.toString();
    }
    return raw;
  } catch {
    return raw;
  }
}

function truthsocialPostUrlFromId(value: unknown) {
  const postId = typeof value === "string" ? value.trim() : "";
  if (!/^\d+$/.test(postId)) return "";
  return `https://truthsocial.com/@realDonaldTrump/posts/${postId}`;
}

function isNonTextRepostPlaceholder(value: string) {
  return /^(RT[:：]?)?$/.test(value.trim()) || /^转发(图像|图片|视频)?内容/.test(value.trim());
}

function isUrlOnlyText(value: string) {
  return /^https?:\/\/\S+$/.test(value.trim());
}

function alertSourceLabel(alert: AlertEvent) {
  const role = String(alert.source_role || "").toUpperCase();
  const roleLabel = role === "BACKUP" || role === "FALLBACK" ? "灾备" : role === "PRIMARY" ? "主源" : "来源";
  return `${roleLabel} ${sourceLabel(alert.source)}`;
}

function trumpsTruthStatusId(item: NewsEvent) {
  if (item.source_name !== "trumps_truth_rss") return "";
  const match = item.url.match(/\/statuses\/(\d+)/);
  return match?.[1] ?? "";
}

function isLegacyTruthMediaItem(item: NewsEvent) {
  if (item.source_name !== "trumps_truth_rss") return false;
  if (item.metadata?.content_kind || item.metadata?.media || item.metadata?.card) return false;
  if (!trumpsTruthStatusId(item)) return false;
  return isGenericNewsTitle(item.title) || isGenericNewsTitle(item.translated_title) || isBrokenLegacyTruthText(item.content) || isBrokenLegacyTruthText(item.translated_summary);
}

function isBrokenLegacyTruthText(value?: string | null) {
  const text = String(value ?? "");
  return text.includes('class="ellipsis"') || text.includes("truthsocial.com/users/");
}

function isDuplicateNewsText(left?: string | null, right?: string | null) {
  const a = normalizeNewsText(left);
  const b = normalizeNewsText(right);
  if (!a || !b) return false;
  if (a === b) return true;
  const shorter = a.length <= b.length ? a : b;
  const longer = a.length > b.length ? a : b;
  return shorter.length >= 30 && longer.includes(shorter);
}

function normalizeNewsText(value?: string | null) {
  return stripHtml(value)
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .replace(/[，。！？、,.!?;；:：'"“”‘’()（）[\]【】《》\-—–_]/g, "")
    .trim()
    .toLowerCase();
}

function stripHtml(value?: string | null) {
  return String(value ?? "").replace(/<[^>]*>/g, " ");
}

function dataSourceLabel(value: string) {
  return DATA_SOURCE_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function activeSourceLabel(sources: Record<string, { source: string; source_role: string }>) {
  const values = Object.values(sources);
  if (!values.length) return "生效源待刷新";
  const sourceNames = Array.from(new Set(values.map((item) => sourceLabel(item.source))));
  const roleNames = Array.from(new Set(values.map((item) => item.source_role)));
  return `${roleNames.includes("BACKUP") ? "灾备生效" : "当前"} ${sourceNames.join("/")}`;
}

function sourceLabel(value: string) {
  const labels: Record<string, string> = {
    okx_swap: "OKX",
    binance_futures: "币安"
  };
  return labels[value] ?? value;
}

function intervalLabel(value: string) {
  return DASHBOARD_INTERVAL_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function WhaleModule({ title, onSelect }: { title: string; onSelect: (id: string) => void }) {
  const { data = [], isLoading, isFetching, dataUpdatedAt, refetch } = useQuery({ queryKey: ["whales"], queryFn: api.whales, refetchInterval: 30_000 });
  return (
    <Panel
      title={title}
      action={
        <ModuleAction
          label={<><PawPrint size={14} /> {data.length} 个地址</>}
          updatedAt={dataUpdatedAt}
          isFetching={isFetching}
          onRefresh={() => { void refetch(); }}
        />
      }
    >
      <div className="whale-grid">
        {data.map((target) => <WhaleTargetCard key={target.id} target={target} onSelect={onSelect} />)}
        {isLoading && <span className="empty">正在加载关注地址…</span>}
        {!isLoading && !data.length && <span className="empty">暂无关注地址。接入巨鲸 API 后会同步操作金额、持仓和动态。</span>}
      </div>
    </Panel>
  );
}

function WhaleTargetCard({ target, onSelect }: { target: WhaleTarget; onSelect: (id: string) => void }) {
  const amount = target.config.current_operation_amount;
  const positions = Array.isArray(target.config.positions) ? target.config.positions : [];
  const holdings = Array.isArray(target.config.holdings) ? target.config.holdings : [];
  const defi = Array.isArray(target.config.defi_positions) ? target.config.defi_positions : [];
  const updatedAt = typeof target.config.last_snapshot_at === "string" ? target.config.last_snapshot_at : target.updated_at;
  const tags = whaleTags(target.config.tags);
  return (
    <button className="whale-card" onClick={() => onSelect(target.id)}>
      <div className="whale-card__head">
        <span className="whale-avatar"><PawPrint size={18} /></span>
        <div>
          <strong>{target.label}</strong>
          <small>{target.address_or_subject}</small>
          <TagPills tags={tags} />
        </div>
        <em>{target.enabled ? "关注中" : "已关闭"}</em>
      </div>
      <div className="whale-stats">
        <div><small>当前操作金额</small><strong>{amount ? `$${formatNumber(amount, 2)}` : "待同步"}</strong></div>
        <div><small>持仓</small><strong>{positions.length} 合约 / {holdings.length + defi.length} 资产</strong></div>
        <div><small>更新</small><strong>{updatedAt ? cnDate(updatedAt) : "待同步"}</strong></div>
      </div>
    </button>
  );
}

function WhaleDetailPage({ targetId, onBack }: { targetId: string; onBack: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["whale", targetId], queryFn: () => api.whaleDetail(targetId) });
  const [tab, setTab] = useState<"basic" | "contracts" | "fills" | "spot" | "orders" | "ledger" | "history" | "events">("contracts");
  if (isLoading || !data) return <div className="boot">正在加载地址详情…</div>;
  const target = data.target;
  const positions = data.positions;
  const holdings = data.holdings;
  const defiPositions = data.defi_positions ?? [];
  const openOrders = data.open_orders ?? [];
  const fills = data.fills ?? [];
  const funding = data.funding ?? [];
  const ledgerUpdates = data.ledger_updates ?? [];
  const historicalOrders = data.historical_orders ?? [];
  const account = data.account_summary ?? {};
  const sourceStatus = data.snapshot?.source_status ?? {};
  const tags = whaleTags(target.config.tags);
  return (
    <div className="whale-detail">
      <div className="whale-detail__hero">
        <button className="icon-button" onClick={onBack} title="返回"><ArrowLeft size={18} /></button>
        <span className="whale-avatar large"><PawPrint size={28} /></span>
        <div>
          <h2>{target.label}</h2>
          <p>{target.address_or_subject}</p>
          <TagPills tags={tags} />
        </div>
        <span className={target.enabled ? "status on" : "status off"}>{target.enabled ? "关注中" : "已关闭"}</span>
      </div>
      <div className="whale-tabs">
        <button className={tab === "basic" ? "active" : ""} onClick={() => setTab("basic")}>基本信息</button>
        <button className={tab === "contracts" ? "active" : ""} onClick={() => setTab("contracts")}>合约 ({positions.length})</button>
        <button className={tab === "fills" ? "active" : ""} onClick={() => setTab("fills")}>最近成交 ({fills.length})</button>
        <button className={tab === "spot" ? "active" : ""} onClick={() => setTab("spot")}>现货/DeFi ({holdings.length + defiPositions.length})</button>
        <button className={tab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>当前委托 ({openOrders.length})</button>
        <button className={tab === "ledger" ? "active" : ""} onClick={() => setTab("ledger")}>资金流水 ({funding.length + ledgerUpdates.length})</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>历史订单 ({historicalOrders.length})</button>
        <button className={tab === "events" ? "active" : ""} onClick={() => setTab("events")}>最近动态</button>
      </div>
      {tab === "basic" && (
        <Panel title="基本信息" dragHandle={false} action={data.updated_at ? <span className="pill">更新 {cnDate(data.updated_at)}</span> : undefined}>
          <div className="whale-summary-grid">
            <Metric label="合约名义价值" value={money(account.contract_notional)} />
            <Metric label="合约未实现盈亏" value={money(account.contract_pnl)} tone={Number(account.contract_pnl ?? 0) >= 0 ? "up" : "down"} />
            <Metric label="现货资产" value={money(account.spot_value ?? account.total_balance)} />
            <Metric label="DeFi 仓位" value={money(account.defi_value)} />
            <Metric label="可提现" value={money(account.withdrawable, { showZero: true })} />
            <Metric label="保证金占用" value={money(account.total_margin_used)} />
          </div>
          <div className="records compact">
            {Object.entries(sourceStatus).map(([source, status]) => {
              const item = status as Record<string, any>;
              return <span key={source}>{source}: {item.ok ? "正常" : String(item.message ?? "未启用")}</span>;
            })}
            {!Object.keys(sourceStatus).length && <span className="empty">暂无数据源状态</span>}
          </div>
        </Panel>
      )}
      {tab === "contracts" && (
        <Panel title="持仓列表" dragHandle={false} action={<span className="pill">共 {positions.length} 个持仓</span>}>
          <div className="position-list">
            {positions.map((position, index) => <PositionCard key={index} position={position} />)}
            {!positions.length && <div className="empty-state">暂无合约持仓。启用巨鲸策略并配置完整 0x 地址后，Hyperliquid 会同步仓位价值、保证金、开仓均价、强平价格、资金费和盈亏。</div>}
          </div>
        </Panel>
      )}
      {tab === "spot" && (
        <Panel title="现货与 DeFi 仓位" dragHandle={false} action={<span className="pill">{holdings.length} 现货 / {defiPositions.length} 协议</span>}>
          <div className="asset-list">
            {holdings.map((holding, index) => <AssetCard key={`holding-${index}`} item={holding} />)}
            {defiPositions.map((item, index) => <AssetCard key={`defi-${index}`} item={item} protocol />)}
            {!holdings.length && !defiPositions.length && <div className="empty-state">暂无多链资产数据。配置 DeBank AccessKey 后会同步现货资产和 DeFi 协议仓位。</div>}
          </div>
        </Panel>
      )}
      {tab === "fills" && (
        <Panel title="最近成交" dragHandle={false} action={<span className="pill">共 {fills.length} 笔</span>}>
          <div className="whale-table">
            {fills.map((fill, index) => <WhaleFillRow key={`${fill.hash ?? fill.trade_id ?? index}`} fill={fill} isLarge={isLargeTrade(fill, target.config)} />)}
            {!fills.length && <span className="empty">暂无最近成交</span>}
          </div>
        </Panel>
      )}
      {tab === "orders" && (
        <Panel title="当前委托" dragHandle={false}>
          <div className="records">
            {openOrders.map((order, index) => <OrderRow key={index} order={order} />)}
            {!openOrders.length && <span className="empty">暂无当前委托</span>}
          </div>
        </Panel>
      )}
      {tab === "ledger" && (
        <Panel title="资金流水与资金费" dragHandle={false}>
          <div className="whale-table">
            {funding.map((item, index) => <FundingRow key={`funding-${index}`} item={item} />)}
            {ledgerUpdates.map((item, index) => <LedgerRow key={`ledger-${index}`} item={item} />)}
            {!funding.length && !ledgerUpdates.length && <span className="empty">暂无资金费或出入金流水</span>}
          </div>
        </Panel>
      )}
      {tab === "history" && (
        <Panel title="历史订单" dragHandle={false}>
          <div className="whale-table">
            {historicalOrders.map((order, index) => <OrderRow key={index} order={order} />)}
            {!historicalOrders.length && <span className="empty">暂无历史订单</span>}
          </div>
        </Panel>
      )}
      {tab === "events" && (
        <Panel title="操作动态" dragHandle={false}>
          <div className="records">
            {data.recent_events.map((event) => <span key={String(event.id)}>{cnDate(event.occurred_at_utc)} · {String(event.summary)}</span>)}
            {!data.recent_events.length && <span className="empty">暂无操作动态</span>}
          </div>
        </Panel>
      )}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div>
      <small>{label}</small>
      <strong className={tone}>{value}</strong>
    </div>
  );
}

function PositionCard({ position }: { position: Record<string, any> }) {
  return (
    <article className="position-card">
      <div className="position-card__head">
        <strong>{String(position.symbol ?? "UNKNOWN")}</strong>
        <span>{String(position.side ?? "方向")}</span>
        <span>{String(position.margin_mode ?? "全仓")}</span>
        <span>{String(position.leverage ?? "--")}x</span>
        <b className={Number(position.pnl ?? 0) >= 0 ? "up" : "down"}>{money(position.pnl)}</b>
      </div>
      <div className="position-grid">
        <div><small>持仓量</small><strong>{String(position.size ?? "--")}</strong></div>
        <div><small>保证金</small><strong>{money(position.margin)}</strong></div>
        <div><small>仓位价值</small><strong>{money(position.notional)}</strong></div>
        <div><small>开仓均价</small><strong>{money(position.entry_price)}</strong></div>
        <div><small>标记价格</small><strong>{money(position.mark_price)}</strong></div>
        <div><small>强平价格</small><strong>{money(position.liquidation_price)}</strong></div>
        <div><small>资金费</small><strong>{money(position.funding)}</strong></div>
        <div><small>强平距离</small><strong>{position.liquidation_distance_pct == null ? "--" : `${formatNumber(position.liquidation_distance_pct, 2)}%`}</strong></div>
      </div>
    </article>
  );
}

function AssetCard({ item, protocol = false }: { item: Record<string, any>; protocol?: boolean }) {
  return (
    <article className="asset-card">
      <div>
        <strong>{String(item.symbol ?? item.name ?? "UNKNOWN")}</strong>
        <small>{protocol ? "DeFi 协议" : String(item.chain ?? "现货资产")}</small>
      </div>
      <b>{money(item.value)}</b>
      {!protocol && <span>{formatNumber(item.amount ?? 0, 4)} @ {money(item.price)}</span>}
      {protocol && <span>{String(item.chain ?? "--")} · {String(item.item_count ?? 0)} 项仓位</span>}
    </article>
  );
}

function WhaleFillRow({ fill, isLarge = false }: { fill: Record<string, any>; isLarge?: boolean }) {
  const positive = Number(fill.closed_pnl ?? 0) >= 0;
  const directionLabel = String(fill.direction_label ?? fill.direction ?? "--");
  const priceLabel = String(fill.price_label ?? "成交价格");
  return (
    <article className="whale-row">
      <div>
        <strong>{directionLabel} {String(fill.coin ?? "")}{isLarge ? " · 大额" : ""}</strong>
        <small>{cnDateFromAny(fill.timestamp)}</small>
      </div>
      <div><small>数量</small><strong>{formatNumber(fill.size ?? 0, 4)}</strong></div>
      <div><small>{priceLabel}</small><strong>{money(fill.price)}</strong></div>
      <div><small>成交额</small><strong>{money(fill.notional)}</strong></div>
      <div><small>手续费</small><strong>{fill.fee == null ? "--" : `${formatNumber(fill.fee, 4)} ${String(fill.fee_token ?? "")}`}</strong></div>
      <div><small>已实现盈亏</small><strong className={positive ? "up" : "down"}>{money(fill.closed_pnl, { showZero: true })}</strong></div>
    </article>
  );
}

function FillRow({ fill, isLarge = false }: { fill: Record<string, any>; isLarge?: boolean }) {
  const positive = Number(fill.closed_pnl ?? 0) >= 0;
  const directionLabel = String(fill.direction_label ?? fill.direction ?? "--");
  const priceLabel = String(fill.price_label ?? "成交价格");
  return (
    <article className="whale-row">
      <div>
        <strong>{String(fill.side ?? "--")} {String(fill.coin ?? "")}{isLarge ? " · 大额" : ""}</strong>
        <small>{cnDateFromAny(fill.timestamp)} · {String(fill.direction ?? "--")}</small>
      </div>
      <div><small>数量</small><strong>{formatNumber(fill.size ?? 0, 4)}</strong></div>
      <div><small>均价</small><strong>{money(fill.price)}</strong></div>
      <div><small>成交额</small><strong>{money(fill.notional)}</strong></div>
      <div><small>手续费</small><strong>{fill.fee == null ? "--" : `${formatNumber(fill.fee, 4)} ${String(fill.fee_token ?? "")}`}</strong></div>
      <div><small>已实现盈亏</small><strong className={positive ? "up" : "down"}>{money(fill.closed_pnl, { showZero: true })}</strong></div>
    </article>
  );
}

function OrderRow({ order }: { order: Record<string, any> }) {
  return (
    <article className="whale-row">
      <div>
        <strong>{String(order.symbol ?? "UNKNOWN")} · {String(order.side ?? "--")}</strong>
        <small>{cnDateFromAny(order.timestamp)} · {String(order.status ?? order.order_type ?? "--")}</small>
      </div>
      <div><small>数量</small><strong>{formatNumber(order.size ?? 0, 4)}</strong></div>
      <div><small>价格</small><strong>{money(order.price)}</strong></div>
      <div><small>名义价值</small><strong>{money(order.notional)}</strong></div>
    </article>
  );
}

function FundingRow({ item }: { item: Record<string, any> }) {
  return (
    <article className="whale-row">
      <div>
        <strong>资金费 {String(item.coin ?? "")}</strong>
        <small>{cnDateFromAny(item.timestamp)}</small>
      </div>
      <div><small>金额</small><strong>{money(item.amount, { showZero: true })}</strong></div>
      <div><small>费率</small><strong>{item.funding_rate == null ? "--" : `${formatNumber(Number(item.funding_rate) * 100, 4)}%`}</strong></div>
      <div><small>仓位</small><strong>{item.position_size == null ? "--" : formatNumber(item.position_size, 4)}</strong></div>
    </article>
  );
}

function LedgerRow({ item }: { item: Record<string, any> }) {
  return (
    <article className="whale-row">
      <div>
        <strong>{String(item.type ?? "流水")}</strong>
        <small>{cnDateFromAny(item.timestamp)}{item.hash ? ` · ${String(item.hash).slice(0, 10)}…` : ""}</small>
      </div>
      <div><small>金额</small><strong>{money(item.amount, { showZero: true })}</strong></div>
    </article>
  );
}

function cnDateFromAny(value: unknown) {
  if (value == null || value === "") return "暂无";
  if (typeof value === "number") return cnDate(new Date(value).toISOString());
  const asNumber = Number(value);
  if (Number.isFinite(asNumber) && String(value).length >= 10) return cnDate(new Date(asNumber).toISOString());
  return cnDate(String(value));
}

function money(value: unknown, options: { showZero?: boolean } = {}) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "--";
  if (num === 0 && !options.showZero) return "--";
  return `$${num.toLocaleString("zh-CN", { maximumFractionDigits: Math.abs(num) >= 100 ? 0 : 2, minimumFractionDigits: 0 })}`;
}

function Admin({ data }: { data: Snapshot }) {
  const [adminToken, setAdminToken] = useState(() => {
    const token = localStorage.getItem("adminToken") ?? "";
    return token && sessionStorage.getItem("adminUnlocked") === "1" ? token : "";
  });
  const [notice, setNotice] = useState("");

  const handleLogin = (token: string) => {
    localStorage.setItem("adminToken", token);
    sessionStorage.setItem("adminUnlocked", "1");
    setAdminToken(token);
    setNotice("管理员登录成功");
  };

  const handleLogout = () => {
    localStorage.removeItem("adminToken");
    sessionStorage.removeItem("adminUnlocked");
    setAdminToken("");
    setNotice("已退出登录");
  };

  if (!adminToken) {
    return (
      <>
        <AdminNoticeToast message={notice} onClose={() => setNotice("")} />
        <AdminLogin onLogin={handleLogin} setNotice={setNotice} />
      </>
    );
  }

  return (
    <>
      <AdminNoticeToast message={notice} onClose={() => setNotice("")} />
      <AdminContent data={data} setNotice={setNotice} onLogout={handleLogout} />
    </>
  );
}

function AdminLogin({ onLogin, setNotice }: { onLogin: (token: string) => void; setNotice: (notice: string) => void }) {
  const [password, setPassword] = useState("");
  const loginMutation = useMutation({
    mutationFn: api.login,
    onSuccess: (result) => {
      setPassword("");
      onLogin(result.token);
    },
    onError: (nextError) => setNotice(`登录失败：${readErrorMessage(nextError)}`)
  });

  return (
    <div className="admin-login-shell">
      <Panel title="管理员登录" dragHandle={false} action={<span className="status off">未登录</span>}>
        <form
          className="login-row"
          onSubmit={(event) => {
            event.preventDefault();
            loginMutation.mutate(password);
          }}
        >
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="管理员密码"
            autoFocus
          />
          <button disabled={!password || loginMutation.isPending}>
            <LogIn size={16} /> {loginMutation.isPending ? "登录中" : "登录"}
          </button>
        </form>
        <p className="hint">登录后才能查看策略参数、机器人、模块显示和翻译配置。</p>
      </Panel>
    </div>
  );
}

function AdminNoticeToast({ message, onClose }: { message: string; onClose: () => void }) {
  if (!message) return null;
  const isError = /失败|错误|密码|不存在|无效|异常|无法|请先|不正确/.test(message);
  return (
    <div className="admin-toast-overlay">
      <div className={isError ? "admin-toast error" : "admin-toast"} role="alert">
        <strong>{isError ? "操作失败" : "操作提示"}</strong>
        <span>{message}</span>
        <button className="icon-button" title="关闭提示" onClick={onClose}><X size={15} /></button>
      </div>
    </div>
  );
}

function AdminContent({
  data,
  setNotice,
  onLogout
}: {
  data: Snapshot;
  setNotice: (notice: string) => void;
  onLogout: () => void;
}) {
  const queryClient = useQueryClient();
  const [symbols, setSymbols] = useState(data.symbols);
  const [strategies, setStrategies] = useState<MutableStrategy[]>(data.strategies as MutableStrategy[]);
  const [modules, setModules] = useState(data.modules);
  const [notifiers, setNotifiers] = useState<NotifierTarget[]>([]);
  const chartModuleIndex = modules.findIndex((module) => module.id === "charts");
  const chartModule = chartModuleIndex >= 0 ? modules[chartModuleIndex] : undefined;

  useEffect(() => {
    setSymbols(data.symbols);
    setStrategies(data.strategies as MutableStrategy[]);
    setModules(data.modules);
  }, [data]);

  const saveSymbols = useMutation({
    mutationFn: api.saveSymbols,
    onSuccess: () => done(queryClient, setNotice, "币种已保存"),
    onError: (error) => setNotice(`币种保存失败：${readErrorMessage(error)}`)
  });
  const saveStrategy = useMutation({
    mutationFn: api.saveStrategy,
    onSuccess: () => done(queryClient, setNotice, "策略已保存"),
    onError: (error) => setNotice(`策略保存失败：${readErrorMessage(error)}`)
  });
  const saveModules = useMutation({
    mutationFn: api.saveModules,
    onSuccess: () => done(queryClient, setNotice, "模块显示已保存"),
    onError: (error) => setNotice(`模块显示保存失败：${readErrorMessage(error)}`)
  });
  const notifiersQuery = useQuery({ queryKey: ["notifiers"], queryFn: api.notifiers });
  const syncSavedNotifiers = (items: NotifierTarget[]) => {
    setNotifiers(items);
    queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    queryClient.invalidateQueries({ queryKey: ["notifiers"] });
  };
  const saveNotifiers = useMutation({
    mutationFn: api.saveNotifiers,
    onSuccess: syncSavedNotifiers
  });
  const testNotifier = useMutation({
    mutationFn: api.testNotifier,
    onSuccess: (result) => setNotice(result.message),
    onError: (error) => setNotice(`测试发送失败：${readErrorMessage(error)}`)
  });

  const testNotifierWithCurrentConfig = (notifier: NotifierTarget) => {
    const notifierId = notifier.id.trim();
    if (!notifierId) {
      setNotice("请先填写机器人 ID");
      return;
    }

    const savedNotifiers = notifiersQuery.data ?? [];
    const hasUnsavedChanges = JSON.stringify(notifiers) !== JSON.stringify(savedNotifiers);
    if (!hasUnsavedChanges) {
      setNotice("");
      testNotifier.mutate(notifierId);
      return;
    }

    setNotice("");
    saveNotifiers.mutate(notifiers, {
      onSuccess: () => testNotifier.mutate(notifierId),
      onError: (error) => setNotice(`测试前保存机器人失败：${readErrorMessage(error)}`)
    });
  };

  useEffect(() => {
    if (notifiersQuery.data) setNotifiers(notifiersQuery.data);
  }, [notifiersQuery.data]);

  return (
    <div className="admin-grid">
      <div className="admin-session">
        <div>
          <strong>后台已解锁</strong>
        </div>
        <button onClick={onLogout}><LogOut size={16} /> 登出</button>
      </div>

      <Panel title="币种管理" dragHandle={false} action={<button onClick={() => saveSymbols.mutate(symbols)}><Save size={16} /> 保存</button>}>
        <div className="table">
          {symbols.map((symbol, index) => (
            <div className="table-row" key={symbol.symbol}>
              <Switch checked={symbol.enabled} onChange={(checked) => updateArray(symbols, setSymbols, index, { enabled: checked })} />
              <input value={symbol.symbol} onChange={(event) => updateArray(symbols, setSymbols, index, { symbol: event.target.value.toUpperCase() })} />
              <input value={symbol.display_name} onChange={(event) => updateArray(symbols, setSymbols, index, { display_name: event.target.value })} />
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="行情数据源" dragHandle={false} action={<button onClick={() => saveModules.mutate(modules)} disabled={!chartModule}><Save size={16} /> 保存</button>}>
        <div className="form-grid">
          <SelectField
            label="首页 K 线"
            value={String(chartModule?.config?.data_source ?? "okx_then_binance")}
            options={DATA_SOURCE_OPTIONS}
            onChange={(data_source) => {
              if (!chartModule || chartModuleIndex < 0) return;
              updateArray(modules, setModules, chartModuleIndex, { config: { ...chartModule.config, data_source } });
            }}
          />
          <span className="hint">这里控制首页五币 K 线墙的数据源；技术策略的数据源在各自策略卡里单独配置。</span>
        </div>
      </Panel>

      <Panel title="策略配置" dragHandle={false} className="strategy-config-panel">
        <div className="strategy-groups">
          {STRATEGY_GROUPS.map((group) => {
            const groupStrategies = group.ids
              .map((strategyId) => {
                const index = strategies.findIndex((strategy) => strategy.id === strategyId);
                return index >= 0 ? { strategy: strategies[index], index } : null;
              })
              .filter((item): item is { strategy: MutableStrategy; index: number } => item !== null);
            if (!groupStrategies.length) return null;
            return (
              <section className="strategy-group" key={group.title}>
                <div className="strategy-group__head">
                  <strong>{group.title}</strong>
                  <span>{groupStrategies.length} 项</span>
                </div>
                <div className="strategy-editors">
                  {groupStrategies.map(({ strategy, index }) => (
                    <StrategyEditor
                      key={strategy.id}
                      strategy={strategy}
                      notifiers={notifiers}
                      onChange={(next) => updateArray(strategies, setStrategies, index, next)}
                      onSave={() => saveStrategy.mutate(strategy)}
                      disabled={false}
                    />
                  ))}
                </div>
                {group.whaleTargets && <WhaleTargetManager setNotice={setNotice} embedded />}
              </section>
            );
          })}
        </div>
      </Panel>

      <Panel
        title="Webhook 机器人"
        dragHandle={false}
        action={
          <div className="panel-actions">
            <button onClick={() => setNotifiers([...notifiers, createNotifier()])}><Plus size={16} /> 新增</button>
            <button
              onClick={() => saveNotifiers.mutate(notifiers, {
                onSuccess: () => setNotice("机器人已保存"),
                onError: (error) => setNotice(`机器人保存失败：${readErrorMessage(error)}`)
              })}
            >
              <Save size={16} /> 保存
            </button>
          </div>
        }
      >
        <div className="notifier-list">
          {notifiers.map((notifier, index) => (
            <div className="notifier-row" key={index}>
              <Switch checked={notifier.enabled} onChange={(checked) => updateArray(notifiers, setNotifiers, index, { enabled: checked })} />
              <input value={notifier.id} onChange={(event) => updateArray(notifiers, setNotifiers, index, { id: slugify(event.target.value) })} placeholder="机器人 ID" />
              <input value={notifier.name} onChange={(event) => updateArray(notifiers, setNotifiers, index, { name: event.target.value })} />
              <select value={notifier.type} onChange={(event) => updateArray(notifiers, setNotifiers, index, { type: event.target.value as "feishu" | "telegram" })}>
                <option value="feishu">飞书</option>
                <option value="telegram">Telegram</option>
              </select>
              <input
                value={notifier.secrets.webhook_url ?? notifier.secrets.bot_token ?? ""}
                onChange={(event) => updateArray(notifiers, setNotifiers, index, { secrets: { ...notifier.secrets, [notifier.type === "feishu" ? "webhook_url" : "bot_token"]: event.target.value } })}
                placeholder={notifier.type === "feishu" ? "Webhook URL" : "Bot Token"}
              />
              {notifier.type === "telegram" && <input value={notifier.secrets.chat_id ?? ""} onChange={(event) => updateArray(notifiers, setNotifiers, index, { secrets: { ...notifier.secrets, chat_id: event.target.value } })} placeholder="Chat ID" />}
              <button disabled={saveNotifiers.isPending || testNotifier.isPending || !notifier.id.trim()} onClick={() => testNotifierWithCurrentConfig(notifier)}><TestTube2 size={16} /> 测试</button>
              <button className="danger-button" onClick={() => setNotifiers(notifiers.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={16} /> 删除</button>
            </div>
          ))}
          {!notifiers.length && <span className="empty">暂无机器人，点击新增后填写 Webhook。</span>}
        </div>
      </Panel>

      <Panel title="模块显示" dragHandle={false} action={<button onClick={() => saveModules.mutate(modules)}><Save size={16} /> 保存</button>}>
        <div className="module-switches">
          {modules.map((module, index) => (
            <div key={module.id} className="module-switch">
              {module.visible ? <Eye size={16} /> : <EyeOff size={16} />}
              <span>{module.title}</span>
              <Switch checked={module.visible} onChange={(visible) => updateArray(modules, setModules, index, { visible })} />
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function WhaleTargetManager({ setNotice, embedded = false }: { setNotice: (notice: string) => void; embedded?: boolean }) {
  const queryClient = useQueryClient();
  const whalesQuery = useQuery({ queryKey: ["whales"], queryFn: api.whales });
  const [drafts, setDrafts] = useState<WhaleTargetUpsert[]>([]);
  const [resolveQuery, setResolveQuery] = useState("");
  const [candidates, setCandidates] = useState<WhaleAddressCandidate[]>([]);
  const persistedTargets = whalesQuery.data ?? [];
  const persistedIds = useMemo(() => new Set(persistedTargets.map((target) => target.id)), [persistedTargets]);
  useEffect(() => {
    if (whalesQuery.data) setDrafts(whalesQuery.data.map(whaleToDraft));
  }, [whalesQuery.data]);

  const saveTarget = useMutation({
    mutationFn: api.saveWhaleTarget,
    onSuccess: () => {
      setNotice("关注对象已保存");
      queryClient.invalidateQueries({ queryKey: ["whales"] });
      queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    },
    onError: (error) => setNotice(`关注对象保存失败：${readErrorMessage(error)}`)
  });
  const deleteTarget = useMutation({
    mutationFn: api.deleteWhaleTarget,
    onSuccess: () => {
      setNotice("关注对象已删除");
      queryClient.invalidateQueries({ queryKey: ["whales"] });
      queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    },
    onError: (error) => setNotice(`关注对象删除失败：${readErrorMessage(error)}`)
  });
  const resolveAddress = useMutation({
    mutationFn: api.resolveWhaleAddress,
    onSuccess: (result) => {
      setCandidates(result.candidates);
      setNotice(result.candidates.length ? `找到 ${result.candidates.length} 个候选地址` : "没有找到完整 0x 地址，请粘贴地址或详情页链接");
    },
    onError: (error) => setNotice(`地址解析失败：${readErrorMessage(error)}`)
  });

  const addCandidate = (candidate: WhaleAddressCandidate) => {
    setDrafts((current) => {
      const draft = createWhaleDraft(candidate);
      const address = candidate.address.toLowerCase();
      const replaceIndex = current.findIndex((target) => {
        const addresses = [target.address_or_subject, ...arr(target.config.addresses)].map((value) => value.toLowerCase());
        return addresses.includes(address)
          || addresses.some((value) => isSameShortAddress(value, address))
          || (isIncompleteWhaleTarget(target) && slugify(target.label) === slugify(candidate.label));
      });
      if (replaceIndex < 0) return [draft, ...current];
      return current.map((target, index) => index === replaceIndex ? mergeWhaleCandidate(target, candidate) : target);
    });
  };

  const removeTarget = (target: WhaleTargetUpsert, index: number) => {
    const duplicateIdCount = target.id ? drafts.filter((item) => item.id === target.id).length : 0;
    const persisted = target.id ? persistedTargets.find((item) => item.id === target.id) : undefined;
    const isEditedDuplicate = !!persisted
      && duplicateIdCount > 1
      && (persisted.label !== target.label || persisted.address_or_subject !== target.address_or_subject);
    if (!target.id || !persistedIds.has(target.id) || isEditedDuplicate) {
      setDrafts((current) => current.filter((_, itemIndex) => itemIndex !== index));
      return;
    }
    setDrafts((current) => current.filter((_, itemIndex) => itemIndex !== index));
    deleteTarget.mutate(target.id, {
      onError: () => queryClient.invalidateQueries({ queryKey: ["whales"] })
    });
  };

  const addButton = <button onClick={() => setDrafts((current) => [createWhaleDraft(), ...current])}><Plus size={16} /> 新增</button>;
  const content = (
      <div className="whale-admin">
        <div className="whale-resolver">
          <input value={resolveQuery} onChange={(event) => setResolveQuery(event.target.value)} placeholder="粘贴 0x 地址、Hyperliquid/Etherscan/DeBank/Arkham 链接，或输入本地昵称" />
          <button disabled={!resolveQuery.trim() || resolveAddress.isPending} onClick={() => resolveAddress.mutate(resolveQuery)}><PawPrint size={16} /> 解析</button>
        </div>
        {!!candidates.length && (
          <div className="candidate-list">
            {candidates.map((candidate, index) => (
              <button key={`${candidate.address}-${index}`} onClick={() => addCandidate(candidate)}>
                <strong>{candidate.label}</strong>
                <span>{candidate.address}</span>
                <small>{candidate.source}</small>
              </button>
            ))}
          </div>
        )}
        <div className="target-list">
          {drafts.map((target, index) => (
            <div className="whale-target-row" key={`${target.id ?? "new"}-${index}`}>
              <Switch checked={target.enabled} onChange={(enabled) => updateArray(drafts, setDrafts, index, { enabled })} />
              <input value={target.label} onChange={(event) => updateArray(drafts, setDrafts, index, { label: event.target.value })} placeholder="名称，例如 麻吉大哥" />
              <input value={target.address_or_subject} onChange={(event) => updateArray(drafts, setDrafts, index, { address_or_subject: event.target.value })} placeholder="主地址（必填）" />
              <WhaleTagSelector value={whaleTags(target.config.tags)} onChange={(tags) => updateArray(drafts, setDrafts, index, { config: { ...target.config, tags } })} />
              <input value={String(target.config.source_url ?? "")} onChange={(event) => updateArray(drafts, setDrafts, index, { config: { ...target.config, source_url: event.target.value } })} placeholder="来源链接（选填，不影响监控）" />
              <button disabled={saveTarget.isPending || !target.label.trim() || !target.address_or_subject.trim()} onClick={() => saveTarget.mutate(target)}><Save size={16} /> 保存</button>
              <button className="danger-button" disabled={deleteTarget.isPending} onClick={() => removeTarget(target, index)}><Trash2 size={16} /> 删除</button>
            </div>
          ))}
          {!drafts.length && <span className="empty">暂无关注对象。先解析地址或点击新增。</span>}
        </div>
      </div>
  );

  if (embedded) {
    return (
      <section className="whale-target-section">
        <div className="whale-target-section__head">
          <strong>关注对象</strong>
          {addButton}
        </div>
        {content}
      </section>
    );
  }

  return (
    <Panel
      title="巨鲸关注对象"
      dragHandle={false}
      className="whale-admin-panel"
      action={addButton}
    >
      {content}
    </Panel>
  );
}

function StrategyEditor({
  strategy,
  notifiers,
  onChange,
  onSave,
  disabled
}: {
  strategy: MutableStrategy;
  notifiers: NotifierTarget[];
  onChange: (strategy: MutableStrategy) => void;
  onSave: () => void;
  disabled: boolean;
}) {
  const config = strategy.config;
  const setConfig = (patch: Record<string, unknown>) => onChange({ ...strategy, config: { ...config, ...patch } });
  const canBindNotifier = strategy.id !== "translation" && strategy.id !== "cleanup";
  const hasReminderIntervals = strategy.id === "kdj" || strategy.id === "ma" || strategy.id === "boll";
  const configuredIntervals = arr(config.intervals);
  const reminderIntervals = strategy.id === "ma" && !configuredIntervals.length ? [String(config.interval ?? "1d")] : configuredIntervals;
  const setReminderIntervals = (intervals: string[]) => {
    if (strategy.id === "ma") {
      setConfig({ intervals, interval: intervals[0] ?? config.interval ?? "1d" });
      return;
    }
    setConfig({ intervals });
  };
  const truthSource = config.enable_truthbrush && config.enable_truth_social ? "both" : config.enable_truthbrush ? "truthbrush" : "rss";
  const setTruthSource = (mode: string) => {
    setConfig({
      enable_truthbrush: mode === "truthbrush" || mode === "both",
      enable_truth_social: mode === "rss" || mode === "both"
    });
  };
  return (
    <div className="strategy-editor">
      <div className="strategy-editor__head">
        <strong>{strategyLabels[strategy.id] ?? strategy.name}</strong>
        <Switch checked={strategy.enabled} onChange={(enabled) => onChange({ ...strategy, enabled })} />
      </div>
      {hasReminderIntervals && (
        <div className="strategy-reminder-top">
          <IntervalMultiSelect label="机器人提醒周期" value={reminderIntervals} onChange={setReminderIntervals} />
        </div>
      )}
      <div className="form-grid">
        {strategy.id === "kdj" && (
          <>
            <NumberField label="N 周期" value={config.period} onChange={(period) => setConfig({ period })} />
            <NumberField label="K 平滑" value={config.k_smoothing} onChange={(k_smoothing) => setConfig({ k_smoothing })} />
            <NumberField label="D 平滑" value={config.d_smoothing} onChange={(d_smoothing) => setConfig({ d_smoothing })} />
            <SelectField label="数据源" value={String(config.data_source ?? "okx_only")} options={DATA_SOURCE_OPTIONS} onChange={(data_source) => setConfig({ data_source })} />
            <Switch checked={Boolean(config.alert_on_live_candle)} onChange={(alert_on_live_candle) => setConfig({ alert_on_live_candle })} label="实时 K 线" />
          </>
        )}
        {strategy.id === "ma" && (
          <>
            <NumberField label="快线" value={config.fast_period} onChange={(fast_period) => setConfig({ fast_period })} />
            <NumberField label="慢线" value={config.slow_period} onChange={(slow_period) => setConfig({ slow_period })} />
            <SelectField label="数据源" value={String(config.data_source ?? "okx_only")} options={DATA_SOURCE_OPTIONS} onChange={(data_source) => setConfig({ data_source })} />
            <Switch checked={Boolean(config.alert_on_live_candle)} onChange={(alert_on_live_candle) => setConfig({ alert_on_live_candle })} label="实时 K 线" />
          </>
        )}
        {strategy.id === "boll" && (
          <>
            <NumberField label="长度" value={config.period} onChange={(period) => setConfig({ period })} />
            <NumberField label="标准差倍数" value={config.stddev} step="0.1" onChange={(stddev) => setConfig({ stddev })} />
            <SelectField label="数据源" value={String(config.data_source ?? "okx_only")} options={DATA_SOURCE_OPTIONS} onChange={(data_source) => setConfig({ data_source })} />
            <Switch checked={Boolean(config.alert_on_live_candle)} onChange={(alert_on_live_candle) => setConfig({ alert_on_live_candle })} label="实时 K 线" />
            <span className="hint">默认按闭合 K 线判断；开启实时 K 线后使用当前未闭合 K 线。</span>
          </>
        )}
        {strategy.id === "trump_social" && (
          <>
            <SelectField label="数据源" value={truthSource} options={TRUTH_SOURCE_OPTIONS} onChange={setTruthSource} />
            {truthSource !== "rss" && <TextField label="TruthBrush 账号" value={String(config.truthsocial_handle ?? "")} onChange={(truthsocial_handle) => setConfig({ truthsocial_handle })} />}
            {truthSource !== "truthbrush" && <TextField label="RSS 地址" value={String(config.truth_social_feed_url ?? "")} onChange={(truth_social_feed_url) => setConfig({ truth_social_feed_url })} />}
            <span className="hint">建议使用 RSS 单源；双源只做容灾，后台仍会去重。</span>
          </>
        )}
        {strategy.id === "whitehouse" && (
          <>
            <TextField label="白宫 Gallery" value={String(config.whitehouse_gallery_url ?? "")} onChange={(whitehouse_gallery_url) => setConfig({ whitehouse_gallery_url })} />
            <TextField label="关键词" value={arr(config.include_keywords).join(",")} onChange={(value) => setConfig({ include_keywords: split(value) })} />
            <TextField label="排除词" value={arr(config.exclude_keywords).join(",")} onChange={(value) => setConfig({ exclude_keywords: split(value) })} />
            <span className="hint">命中任一关键词才进入白宫新闻；关键词留空则不过滤。</span>
          </>
        )}
        {strategy.id === "translation" && (
          <>
            <Switch checked={Boolean(config.enabled)} onChange={(enabled) => setConfig({ enabled })} label="启用翻译" />
            <TextField label="API 地址" value={String(config.api_url ?? "")} onChange={(api_url) => setConfig({ api_url })} />
            <TextField label="模型名" value={String(config.model ?? "")} onChange={(model) => setConfig({ model })} />
            <TextField label="API Key" value={String(config.api_key ?? "")} onChange={(api_key) => setConfig({ api_key })} />
          </>
        )}
        {strategy.id === "cleanup" && (
          <>
            <TextField label="执行时间（北京时间）" value={String(config.schedule_time ?? "12:30")} onChange={(schedule_time) => setConfig({ schedule_time })} />
            <NumberField label="告警保留天数" value={config.alert_retention_days ?? 30} onChange={(alert_retention_days) => setConfig({ alert_retention_days })} />
            <NumberField label="新闻保留天数" value={config.news_retention_days ?? 60} onChange={(news_retention_days) => setConfig({ news_retention_days })} />
            <NumberField label="巨鲸事件保留天数" value={config.whale_retention_days ?? 90} onChange={(whale_retention_days) => setConfig({ whale_retention_days })} />
            <Switch checked={Boolean(config.delete_pending_notifications ?? true)} onChange={(delete_pending_notifications) => setConfig({ delete_pending_notifications })} label="清理未推送超期记录" />
            <Switch checked={Boolean(config.vacuum_after_cleanup ?? true)} onChange={(vacuum_after_cleanup) => setConfig({ vacuum_after_cleanup })} label="清理后压缩数据库" />
            <span className="hint">每天按北京时间执行；默认 12:30。不会清理策略配置、机器人、布局或系统健康状态。</span>
          </>
        )}
        {strategy.id === "whale" && (
          <>
            <Switch checked={Boolean(config.enabled)} onChange={(enabled) => setConfig({ enabled })} label="启用巨鲸" />
            <Switch checked={Boolean(config.hyperliquid_enabled ?? true)} onChange={(hyperliquid_enabled) => setConfig({ hyperliquid_enabled })} label="Hyperliquid 合约" />
            <TextField label="Hyperliquid API" value={String(config.hyperliquid_base_url ?? "https://api.hyperliquid.xyz")} onChange={(hyperliquid_base_url) => setConfig({ hyperliquid_base_url })} />
            <Switch checked={Boolean(config.debank_enabled)} onChange={(debank_enabled) => setConfig({ debank_enabled })} label="DeBank 多链资产" />
            <TextField label="DeBank API" value={String(config.debank_base_url ?? "https://pro-openapi.debank.com")} onChange={(debank_base_url) => setConfig({ debank_base_url })} />
            <TextField label="DeBank AccessKey" value={String(config.debank_access_key ?? "")} onChange={(debank_access_key) => setConfig({ debank_access_key })} />
            <NumberField label="主轮询秒" value={config.poll_seconds ?? 300} onChange={(poll_seconds) => setConfig({ poll_seconds: Math.max(300, poll_seconds) })} />
            <Switch checked={Boolean(config.trade_monitor_enabled ?? true)} onChange={(trade_monitor_enabled) => setConfig({ trade_monitor_enabled })} label="成交监控" />
            <Switch checked={Boolean(config.trade_notification_enabled ?? true)} onChange={(trade_notification_enabled) => setConfig({ trade_notification_enabled })} label="成交机器人提醒" />
            <NumberField label="成交轮询秒" value={config.trade_poll_seconds ?? 120} onChange={(trade_poll_seconds) => setConfig({ trade_poll_seconds: Math.max(120, trade_poll_seconds) })} />
            <NumberField label="扩展信息轮询秒" value={config.extended_poll_seconds ?? 1800} onChange={(extended_poll_seconds) => setConfig({ extended_poll_seconds: Math.max(1800, extended_poll_seconds) })} />
            <NumberField label="前台大额美元阈值" value={config.trade_min_notional_usd ?? 100000} onChange={(trade_min_notional_usd) => setConfig({ trade_min_notional_usd })} />
            <NumberField label="ETH 数量阈值" value={tradeCoinThreshold(config, "ETH", 100)} onChange={(value) => setConfig({ trade_coin_thresholds: { ...tradeCoinThresholds(config), ETH: value } })} />
            <NumberField label="BTC 数量阈值" value={tradeCoinThreshold(config, "BTC", 5)} onChange={(value) => setConfig({ trade_coin_thresholds: { ...tradeCoinThresholds(config), BTC: value } })} />
            <NumberField label="SOL 数量阈值" value={tradeCoinThreshold(config, "SOL", 10000)} onChange={(value) => setConfig({ trade_coin_thresholds: { ...tradeCoinThresholds(config), SOL: value } })} />
            <SelectField
              label="首次成交同步"
              value={String(config.initial_fill_sync_mode ?? "cursor_only")}
              options={[
                { value: "cursor_only", label: "只记录游标" },
                { value: "lookback_3h", label: "回看 3 小时" },
                { value: "today", label: "今天全量" }
              ]}
              onChange={(initial_fill_sync_mode) => setConfig({ initial_fill_sync_mode })}
            />
            <NumberField label="仓位变化告警 %" value={config.position_change_alert_pct ?? 25} onChange={(position_change_alert_pct) => setConfig({ position_change_alert_pct })} />
            <NumberField label="最小仓位美元" value={config.min_position_value_usd ?? 10000} onChange={(min_position_value_usd) => setConfig({ min_position_value_usd })} />
            <NumberField label="强平距离 %" value={config.liquidation_distance_pct ?? 5} onChange={(liquidation_distance_pct) => setConfig({ liquidation_distance_pct })} />
          </>
        )}
      </div>
      <div className="strategy-editor__footer">
        {canBindNotifier ? (
          <select value={strategy.notifier_id ?? ""} onChange={(event) => onChange({ ...strategy, notifier_id: event.target.value || null })}>
            <option value="">不绑定机器人</option>
            {notifiers.map((notifier) => <option value={notifier.id} key={notifier.id}>{notifier.name}</option>)}
          </select>
        ) : (
          <span className="hint">{strategy.id === "cleanup" ? "清理策略不绑定机器人；只在后台定时维护数据库容量。" : "大模型翻译只处理社媒和新闻文本，不绑定通知机器人。"}</span>
        )}
        <button onClick={onSave} disabled={disabled}><Save size={16} /> 保存</button>
      </div>
    </div>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function NumberField({ label, value, onChange, step = "1" }: { label: string; value: unknown; onChange: (value: number) => void; step?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" step={step} value={Number(value ?? 0)} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

function IntervalSegmented({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const activeIndex = Math.max(0, DASHBOARD_INTERVAL_OPTIONS.findIndex((option) => option.value === value));
  const thumbTransform = `translateX(calc(${activeIndex * 100}% + ${activeIndex * 4}px))`;

  return (
    <div className="interval-segmented">
      <span className="interval-segmented__thumb" style={{ transform: thumbTransform }} />
      {DASHBOARD_INTERVAL_OPTIONS.map((option) => (
        <button key={option.value} type="button" className={value === option.value ? "active" : ""} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

function StrategyIntervalControl({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="strategy-cycle-select">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {DASHBOARD_INTERVAL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

function IntervalMultiSelect({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  return (
    <fieldset className="field check-group">
      <legend>{label}</legend>
      <div>
        {INTERVAL_OPTIONS.map((interval) => (
          <label key={interval}>
            <input
              type="checkbox"
              checked={value.includes(interval)}
              onChange={(event) => {
                const next = event.target.checked ? [...value, interval] : value.filter((item) => item !== interval);
                onChange(INTERVAL_OPTIONS.filter((item) => next.includes(item)));
              }}
            />
            <span>{interval}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function arr(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function tradeCoinThresholds(config: Record<string, any>): Record<string, number> {
  return typeof config.trade_coin_thresholds === "object" && config.trade_coin_thresholds !== null
    ? config.trade_coin_thresholds as Record<string, number>
    : {};
}

function tradeCoinThreshold(config: Record<string, any>, coin: string, fallback: number) {
  const thresholds = tradeCoinThresholds(config);
  const value = Number(thresholds[coin]);
  return Number.isFinite(value) ? value : fallback;
}

function isLargeTrade(fill: Record<string, any>, config: Record<string, any>) {
  const notional = Number(fill.notional ?? 0);
  const usdThreshold = Number(config.trade_min_notional_usd ?? 100000);
  if (Number.isFinite(notional) && Number.isFinite(usdThreshold) && notional >= usdThreshold) return true;
  const coin = String(fill.coin ?? "").toUpperCase();
  const size = Math.abs(Number(fill.size ?? 0));
  const threshold = tradeCoinThreshold(config, coin, 0);
  return threshold > 0 && Number.isFinite(size) && size >= threshold;
}

function whaleTags(value: unknown): string[] {
  const tags = arr(value).filter((tag) => WHALE_TAG_OPTIONS.includes(tag));
  return tags.length ? tags : ["聪明钱"];
}

function toggleWhaleTag(tags: string[], tag: string) {
  const current = new Set(whaleTags(tags));
  if (current.has(tag)) current.delete(tag);
  else current.add(tag);
  return current.size ? WHALE_TAG_OPTIONS.filter((item) => current.has(item)) : ["聪明钱"];
}

function TagPills({ tags }: { tags: string[] }) {
  return (
    <span className="tag-pills">
      {whaleTags(tags).map((tag) => <span key={tag}>{tag}</span>)}
    </span>
  );
}

function WhaleTagSelector({ value, onChange }: { value: string[]; onChange: (tags: string[]) => void }) {
  const tags = whaleTags(value);
  return (
    <details className="tag-dropdown">
      <summary>{tags.join("、")}</summary>
      <div className="tag-dropdown__menu">
        {WHALE_TAG_OPTIONS.map((tag) => (
          <label key={tag}>
            <input
              type="checkbox"
              checked={tags.includes(tag)}
              onChange={() => onChange(toggleWhaleTag(tags, tag))}
            />
            <span>{tag}</span>
          </label>
        ))}
      </div>
    </details>
  );
}

function split(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function createNotifier(): NotifierTarget {
  const suffix = Date.now().toString(36);
  return {
    id: `feishu-${suffix}`,
    name: "新机器人",
    type: "feishu",
    enabled: false,
    secrets: { webhook_url: "" },
    created_at: "",
    updated_at: ""
  };
}

function whaleToDraft(target: WhaleTarget): WhaleTargetUpsert {
  return {
    id: target.id,
    label: target.label,
    address_or_subject: target.address_or_subject,
    enabled: target.enabled,
    config: {
      ...target.config,
      tags: whaleTags(target.config.tags),
      addresses: arr(target.config.addresses)
    }
  };
}

function createWhaleDraft(candidate?: WhaleAddressCandidate): WhaleTargetUpsert {
  const address = candidate?.address ?? "";
  return {
    id: null,
    label: candidate?.label ?? "新关注对象",
    address_or_subject: address,
    enabled: true,
    config: {
      tags: ["聪明钱"],
      addresses: address ? [address] : [],
      source_url: candidate?.url ?? ""
    }
  };
}

function mergeWhaleCandidate(target: WhaleTargetUpsert, candidate: WhaleAddressCandidate): WhaleTargetUpsert {
  const address = candidate.address.toLowerCase();
  return {
    ...target,
    label: target.label.trim() ? target.label : candidate.label,
    address_or_subject: candidate.address,
    config: {
      ...target.config,
      tags: whaleTags(target.config.tags),
      addresses: Array.from(new Set([address, ...arr(target.config.addresses).map((item) => item.toLowerCase())])),
      source_url: target.config.source_url || candidate.url || ""
    }
  };
}

function isIncompleteWhaleTarget(target: WhaleTargetUpsert) {
  const values = [target.address_or_subject, ...arr(target.config.addresses)];
  return !values.some((value) => /^0x[a-fA-F0-9]{40}$/.test(value)) || values.some((value) => value.includes("..."));
}

function isSameShortAddress(value: string, fullAddress: string) {
  const match = value.toLowerCase().match(/^(0x[a-f0-9]{4,})\.\.\.([a-f0-9]{4,})$/);
  return !!match && fullAddress.toLowerCase().startsWith(match[1]) && fullAddress.toLowerCase().endsWith(match[2]);
}

function slugify(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
}

function updateArray<T>(items: T[], setItems: (items: T[]) => void, index: number, patch: Partial<T>) {
  setItems(items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
}

function done(queryClient: ReturnType<typeof useQueryClient>, setNotice: (value: string) => void, message: string) {
  setNotice(message);
  queryClient.invalidateQueries({ queryKey: ["snapshot"] });
  queryClient.invalidateQueries({ queryKey: ["notifiers"] });
}

function readErrorMessage(error: unknown) {
  const raw = error instanceof Error ? error.message : String(error || "操作失败");
  if (/failed to fetch|networkerror|load failed/i.test(raw)) {
    return "网络请求失败，请确认后端服务正在运行。";
  }
  try {
    const parsed = JSON.parse(raw) as { detail?: string | Array<Record<string, unknown>> };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail)) return formatValidationErrors(parsed.detail);
  } catch {
    // Fall through to plain-text handling.
  }
  if (raw.includes("401")) return "管理员登录已失效，请重新登录。";
  if (raw.includes("404")) return "请求的配置不存在，请刷新后重试。";
  if (raw.includes("422")) return "提交的数据格式不正确，请检查必填项。";
  return raw || "操作失败，请稍后重试。";
}

function formatValidationErrors(details: Array<Record<string, unknown>>) {
  const messages = details.map((detail) => {
    const type = String(detail.type ?? "");
    const loc = Array.isArray(detail.loc) ? detail.loc.map(String) : [];
    const field = loc[loc.length - 1] ?? "";
    const label = fieldLabel(field);
    if (type === "list_type") return "提交内容格式不正确：需要提交列表数据。";
    if (type === "missing") return `缺少必填字段：${label}`;
    if (type === "literal_error") return `${label} 的取值不支持。`;
    if (type.endsWith("_type")) return `${label} 的数据类型不正确。`;
    if (type.includes("max_length")) return `${label} 超过允许长度。`;
    return String(detail.msg ?? "提交的数据格式不正确。");
  });
  return Array.from(new Set(messages)).join("；") || "提交的数据格式不正确。";
}

function fieldLabel(field: string) {
  const labels: Record<string, string> = {
    body: "请求内容",
    id: "机器人 ID",
    name: "机器人名称",
    type: "机器人类型",
    enabled: "启用状态",
    secrets: "密钥配置",
    webhook_url: "Webhook URL",
    bot_token: "Telegram Bot Token",
    chat_id: "Telegram Chat ID",
    password: "管理员密码",
    ids: "新闻 ID 列表"
  };
  return labels[field] ?? (field || "字段");
}

export default App;
