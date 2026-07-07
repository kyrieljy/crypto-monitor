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
  BtcLargeTransfer,
  DashboardModule,
  IbitHistorySyncJobStatus,
  IbitHistorySyncResult,
  NewsEvent,
  NotifierTarget,
  Snapshot,
  SourceHealth,
  StrategyConfig,
  ThemeMode,
  WhaleAddressCandidate,
  WhaleDetail,
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

type BtcAddressView = {
  targetId: string;
  targetLabel: string;
  address: string;
  label: string;
  role: "confirmed" | "suspected";
  confidence: number;
  operations: Array<Record<string, any>>;
  signals: Array<Record<string, any>>;
  reasons: string[];
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
  const [notice, setNotice] = useState("");
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
      <AdminNoticeToast message={notice} onClose={() => setNotice("")} />
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
      {data && view === "dashboard" && selectedWhaleId && <WhaleDetailPage targetId={selectedWhaleId} onBack={() => setSelectedWhaleId(null)} setNotice={setNotice} />}
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
    /无法翻译/.test(value) ||
    /无法处理[：:]/.test(value) ||
    /需要提供.*英文新闻标题或摘要/.test(value) ||
    /未提供.*英文新闻标题或摘要/.test(value) ||
    /未提供.*需要翻译/.test(value) ||
    /未提供.*英文文本/.test(value) ||
    /请.*提供.*英文新闻标题或摘要/.test(value) ||
    /请提供.*英文新闻标题或摘要/.test(value) ||
    /请提供.*需要翻译的内容/.test(value) ||
    /请提供.*需要翻译的英文文本/.test(value) ||
    /请提供.*英文文本/.test(value) ||
    /无法访问外部链接/.test(value) ||
    /链接是.*帖子/.test(value) ||
    /作为\s*AI.*无法访问/.test(value) ||
    /不是.*英文新闻标题或摘要/.test(value) ||
    /重新提供.*英文内容/.test(value) ||
    /英文内容.*以便翻译/.test(value) ||
    /未包含任何需要翻译/.test(value) ||
    /没有.*可翻译的内容/.test(value) ||
    /并非仅链接/.test(value) ||
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
  const text = value.trim();
  if (/^https?:\/\/\S+$/.test(text)) return true;
  return /^(?:https?:\/\/)?(?:[\w-]+\.)+[a-z]{2,}\/\S+$/i.test(text.replace(/\s+/g, ""));
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

function BtcAddressDetailCard({
  row,
  detail,
  canAdmin,
  subscribing,
  onSubscribe
}: {
  row: BtcAddressView;
  detail: WhaleDetail;
  canAdmin: boolean;
  subscribing: boolean;
  onSubscribe: () => void;
}) {
  const account = detail.account_summary ?? {};
  const raw = detail.snapshot?.raw ?? {};
  const flow = raw.farside && typeof raw.farside === "object" ? raw.farside as Record<string, any> : {};
  const latest = row.operations[0];
  return (
    <div className="btc-address-detail-card">
      <div className="btc-address-detail-card__head">
        <div>
          <strong>{row.label}</strong>
          <small>{row.address}</small>
        </div>
        {row.role !== "confirmed" && canAdmin && <button disabled={subscribing} onClick={onSubscribe}><Plus size={14} /> 订阅地址</button>}
      </div>
      <div className="btc-address-detail-grid">
        <Metric label="最近方向" value={latest ? String(latest.behavior ?? latest.direction ?? "--") : "--"} />
        <Metric label="最近金额" value={latest ? `${formatNumber(latest.amount_btc, 4)} BTC` : "--"} />
        <Metric label="最近净额" value={latest ? `${formatNumber(latest.net_btc, 4)} BTC` : "--"} />
        <Metric label="是否确认" value={latest ? (latest.confirmed ? "已确认" : "未确认") : "--"} />
        <Metric label="ETF净流入/流出" value={money(account.blackrock_last_flow_usd)} tone={Number(account.blackrock_last_flow_usd ?? 0) >= 0 ? "up" : "down"} />
        <Metric label="iShares估算持仓" value={account.blackrock_official_estimated_btc == null ? "--" : `${formatNumber(account.blackrock_official_estimated_btc, 2)} BTC`} />
      </div>
      <div className="btc-address-op-table">
        {row.operations.slice(0, 8).map((operation) => (
          <BtcAddressOperationRow key={`${row.address}-${operation.txid}-${operation.direction}`} operation={operation} />
        ))}
        {!row.operations.length && <span className="empty">暂无该地址底单。</span>}
      </div>
      <div className="btc-address-news-list">
        {row.signals.slice(0, 4).map((signal) => (
          <span key={newsSignalKey(signal)}>
            {cnDateFromAny(signal.published_at)} · {String(signal.title ?? "新闻线索")} · 置信度 {formatNumber(Number(signal.confidence ?? row.confidence ?? 0) * 100, 0)}%
          </span>
        ))}
        {!row.signals.length && <span className="empty">暂无新闻线索匹配。</span>}
      </div>
      {flow.date && <small className="btc-address-flow-note">Farside ETF资金流: {String(flow.date)} · IBIT {money(Number(flow.ibit_flow_usd_m ?? 0) * 1_000_000, { showZero: true })}</small>}
    </div>
  );
}

function BtcAddressOperationRow({ operation }: { operation: Record<string, any> }) {
  const counterparties = btcCounterpartyText(operation);
  return (
    <div className="btc-address-op-row">
      <strong>{cnDateFromAny(operation.timestamp ?? operation.timestamp_ms)}</strong>
      <span>{String(operation.behavior ?? operation.direction ?? "--")}</span>
      <span>{formatNumber(operation.amount_btc, 4)} BTC</span>
      <span>净额 {formatNumber(operation.net_btc, 4)} BTC</span>
      <span>{operation.confirmed ? "已确认" : "未确认"}</span>
      <small title={String(operation.txid ?? "")}>txid {shortText(operation.txid, 12)}</small>
      {counterparties && <small title={counterparties}>对手方 {counterparties}</small>}
      {operation.source_url && <a href={String(operation.source_url)} target="_blank" rel="noreferrer">Blockstream</a>}
    </div>
  );
}

function IbitBtcAddressPanel({
  target,
  detail,
  mode,
  setNotice
}: {
  target: WhaleTarget;
  detail: WhaleDetail;
  mode: "confirmed" | "suspected";
  setNotice: (notice: string) => void;
}) {
  const queryClient = useQueryClient();
  const canAdmin = Boolean(localStorage.getItem("adminToken"));
  const allRows = useMemo(() => ibitAddressRows(target, detail), [target, detail]);
  const rows = mode === "confirmed" ? allRows.filter((row) => row.role === "confirmed") : allRows.filter((row) => row.role !== "confirmed");
  const [expandedAddress, setExpandedAddress] = useState("");
  useEffect(() => {
    if (expandedAddress && !rows.some((row) => row.address === expandedAddress)) setExpandedAddress("");
  }, [rows, expandedAddress]);
  const subscribe = useMutation({
    mutationFn: (row: BtcAddressView) => api.confirmWhaleBtcAddress(row.targetId, row.address, "confirmed", row.label || "贝莱德 / IBIT"),
    onSuccess: (_result, row) => {
      setNotice(`订阅成功：${shortText(row.address, 10)} 已加入监控`);
      void queryClient.invalidateQueries({ queryKey: ["whale", target.id] });
      void queryClient.invalidateQueries({ queryKey: ["whales"] });
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    },
    onError: (error) => setNotice(`订阅失败：${readErrorMessage(error)}`)
  });
  const subscribeRow = (row: BtcAddressView) => {
    if (isSubscribedBtcAddress(target, row.address)) {
      setNotice(`这个 BTC 地址已经在监控中：${shortText(row.address, 10)}`);
      return;
    }
    subscribe.mutate(row);
  };
  const title = mode === "confirmed" ? "BTC链订阅" : "疑似地址";
  const operationCount = rows.reduce((sum, row) => sum + row.operations.length, 0);
  return (
    <Panel title={title} dragHandle={false}>
      <div className="whale-summary-grid">
        <Metric label="地址数" value={String(rows.length)} />
        <Metric label="底单数" value={String(operationCount)} />
        <Metric label="新闻线索" value={String(rows.reduce((sum, row) => sum + row.signals.length, 0))} />
        <Metric label="提醒置信度" value={`${formatNumber((target.config.ibit_news_candidate_notify_min_confidence ?? 0.7) * 100, 0)}%`} />
      </div>
      <div className="btc-address-table">
        {rows.map((row) => {
          const expanded = expandedAddress === row.address;
          const latest = row.operations[0];
          return (
            <div className="btc-address-table__item" key={row.address}>
              <button className={expanded ? "btc-address-table__row active" : "btc-address-table__row"} onClick={() => setExpandedAddress(expanded ? "" : row.address)}>
                <span>
                  <strong>{row.label}</strong>
                  <small>{shortText(row.address, 14)}</small>
                </span>
                <em className={row.role === "confirmed" ? "status on" : "status off"}>{row.role === "confirmed" ? "已订阅" : "疑似"}</em>
                <span>{row.operations.length} 笔底单</span>
                <span>{row.signals.length} 条新闻</span>
                <span>{latest ? `${cnDateFromAny(latest.timestamp ?? latest.timestamp_ms)} · ${String(latest.behavior ?? latest.direction ?? "--")} ${formatNumber(latest.amount_btc, 4)} BTC` : "暂无操作"}</span>
                <span>{row.confidence ? `置信度 ${formatNumber(row.confidence * 100, 0)}%` : "手动/配置"}</span>
              </button>
              {expanded && (
                <div className="btc-address-table__detail">
                  <BtcAddressDetailCard
                    row={row}
                    detail={detail}
                    canAdmin={canAdmin}
                    subscribing={subscribe.isPending}
                    onSubscribe={() => subscribeRow(row)}
                  />
                </div>
              )}
            </div>
          );
        })}
        {!rows.length && <span className="empty">{mode === "confirmed" ? "暂无已订阅 BTC 地址。" : "暂无疑似地址。后台手动添加的疑似地址会在这里显示，订阅后会转入 BTC 地址簇。"}</span>}
      </div>
    </Panel>
  );
}

function NewsSignalMatchList({
  matches,
  target,
  canAdmin,
  subscribing,
  onSubscribe
}: {
  matches: Array<{ item: Record<string, any>; signal: Record<string, any>; confidence: number }>;
  target: WhaleTarget;
  canAdmin: boolean;
  subscribing: boolean;
  onSubscribe: (address: string, label: string) => void;
}) {
  const subscribed = subscribedBtcAddressSet(target);
  return (
    <div className="news-match-address-list">
      {matches.map(({ item, signal, confidence }) => {
        const address = String(item.address ?? "");
        const largeMatch = signal.large_transfer_match && typeof signal.large_transfer_match === "object" ? signal.large_transfer_match as Record<string, any> : {};
        const operation = signal.operation && typeof signal.operation === "object" ? signal.operation as Record<string, any> : Array.isArray(item.latest_operations) ? item.latest_operations[0] : undefined;
        const reasons = Array.isArray(signal.match_reasons) && signal.match_reasons.length ? signal.match_reasons : Array.isArray(item.reasons) ? item.reasons : [];
        const label = inferredBtcAddressLabel(signal, target);
        const isBtc = isBtcAddress(address);
        const isSubscribed = subscribed.has(normalizeAddress(address));
        const operationInfo = newsMatchOperationInfo(operation, largeMatch, signal);
        return (
          <div className="news-match-address-row" key={address}>
            <div>
              <strong>{label}</strong>
              <small>{address}</small>
            </div>
            <div className="news-match-address-row__metrics">
              <span>置信度 {formatNumber(confidence * 100, 0)}%</span>
              <span>{operationInfo.kind}</span>
              {Array.isArray(signal.txids) && signal.txids.length ? <span>txid {signal.txids.map((txid: unknown) => shortText(txid, 10)).join(", ")}</span> : null}
            </div>
            <div className="news-match-address-row__behavior">
              <b>命中行为</b>
              <span>{operationInfo.summary}</span>
            </div>
            <p>匹配依据：{reasons.slice(0, 4).join("；") || "新闻与链上底单存在相似时间、金额或方向特征。"}</p>
            <div className="news-match-address-row__actions">
              {operationInfo.sourceUrl && <a href={operationInfo.sourceUrl} target="_blank" rel="noreferrer">{operationInfo.explorerLabel}</a>}
              {!isBtc ? <span className="status off">ETH候选</span> : isSubscribed ? <span className="status on">已订阅</span> : canAdmin ? <button disabled={subscribing || !address} onClick={() => onSubscribe(address, label)}><Plus size={14} /> 订阅地址</button> : <span className="status off">未订阅</span>}
            </div>
          </div>
        );
      })}
      {!matches.length && <span className="empty">这条新闻暂无匹配地址。</span>}
    </div>
  );
}

function newsMatchOperationInfo(operation: Record<string, any> | undefined, largeMatch: Record<string, any>, signal: Record<string, any>) {
  const transfer = largeMatch.transfer && typeof largeMatch.transfer === "object" ? largeMatch.transfer as Record<string, any> : {};
  const source = operation ?? transfer;
  const asset = transferAsset(source);
  const role = String(largeMatch.address_role ?? operation?.address_role ?? "");
  const roleText = role === "source" ? "资金来源地址" : role === "receiver" ? "接收地址" : role === "input" ? "输入地址" : role === "output" ? "输出地址" : "匹配地址";
  const behavior = String(operation?.behavior ?? largeMatch.behavior ?? signal.behavior ?? (role === "source" ? "转出" : role === "receiver" ? "转入" : "链上操作"));
  const amount = Number(operation?.amount ?? operation?.amount_btc ?? operation?.amount_eth ?? largeMatch.address_value ?? largeMatch.address_value_btc ?? largeMatch.address_value_eth ?? transfer.amount ?? transfer.amount_btc ?? 0);
  const timeValue = operation?.timestamp ?? operation?.timestamp_ms ?? operation?.block_time_utc ?? transfer.block_time_utc ?? signal.published_at;
  const txid = String(operation?.txid ?? largeMatch.txid ?? transfer.txid ?? (Array.isArray(signal.txids) ? signal.txids[0] : "") ?? "");
  const sourceUrl = String(operation?.source_url ?? largeMatch.source_url ?? transfer.source_url ?? "");
  const explorerLabel = transferExplorerLabel(source);
  const actionText = [
    cnDateFromAny(timeValue),
    roleText,
    behavior,
    amount > 0 ? `${formatNumber(amount, 4)} ${asset}` : "",
    txid ? `txid ${shortText(txid, 12)}` : "",
  ].filter(Boolean).join(" · ");
  return {
    kind: Object.keys(largeMatch).length ? "链上大额底表" : operation ? "账户底单" : "新闻文本匹配",
    summary: actionText || "未关联到单笔链上行为，只命中新闻关键词、金额或 txid。",
    sourceUrl: /^https?:\/\//i.test(sourceUrl) ? sourceUrl : "",
    explorerLabel,
  };
}

function NewsSignalOriginal({ signal }: { signal: Record<string, any> }) {
  const url = String(signal.url ?? "");
  const source = String(signal.source ?? "");
  const text = String(signal.original_text ?? signal.summary ?? "").trim();
  const canOpen = /^https?:\/\//i.test(url);
  return (
    <div className="news-signal-original">
      <div className="news-signal-original__head">
        <div>
          <strong>原文线索</strong>
          <small>{[source, cnDateFromAny(signal.published_at)].filter(Boolean).join(" · ")}</small>
        </div>
        {canOpen && <a href={url} target="_blank" rel="noreferrer">打开原文</a>}
      </div>
      <p>{text || "这条新闻源没有提供正文，只能通过原文链接查看。"}</p>
    </div>
  );
}

function BtcTransferNewsMatches({ matches }: { matches: Array<Record<string, any>> }) {
  const [expandedKey, setExpandedKey] = useState("");
  if (!matches.length) {
    return (
      <div className="btc-transfer-news-matches">
        <span className="empty">这笔交易暂未命中 IBIT 新闻线索。</span>
      </div>
    );
  }
  return (
    <div className="btc-transfer-news-matches">
      <div className="btc-transfer-news-matches__head">
        <strong>命中新闻</strong>
        <span>{matches.length} 条</span>
      </div>
      {matches.map((match, index) => {
        const rawSignal = match.signal ?? match.news_signal;
        const signal = rawSignal && typeof rawSignal === "object" ? rawSignal as Record<string, any> : {};
        const displaySignal = { ...signal, published_at: signal.published_at ?? match.published_at_utc };
        const title = String(signal.title ?? match.signal_id ?? "IBIT 新闻线索");
        const key = `${String(match.signal_id ?? signal.id ?? title)}-${String(match.candidate_address ?? "")}-${index}`;
        const expanded = expandedKey === key;
        const confidence = Number(match.confidence ?? 0);
        const reasons = Array.isArray(match.reasons) ? match.reasons.map((item) => String(item)).filter(Boolean) : [];
        const asset = String(match.asset ?? (match.transfer as Record<string, any> | undefined)?.asset ?? "BTC").toUpperCase();
        const role = String(match.address_role ?? "");
        const roleText = role === "input" ? "输入地址" : role === "output" ? "输出地址" : "命中地址";
        const address = String(match.candidate_address ?? "");
        const addressValue = Number(match.address_value ?? match.address_value_btc ?? match.address_value_eth ?? 0);
        const dateText = cnDateFromAny(displaySignal.published_at);
        return (
          <article className="btc-transfer-news-match" key={key}>
            <button className="btc-transfer-news-match__button" onClick={() => setExpandedKey(expanded ? "" : key)}>
              <span>
                <strong>{title}</strong>
                <small>
                  {dateText} · 置信度 {formatNumber(confidence * 100, 0)}% · {roleText} {address ? shortText(address, 10) : "--"}
                  {addressValue > 0 ? ` · ${formatNumber(addressValue, 4)} ${asset}` : ""}
                </small>
              </span>
              <em>{expanded ? "收起原文" : "查看原文"}</em>
            </button>
            {reasons.length > 0 && (
              <div className="btc-transfer-news-match__reasons">
                {reasons.slice(0, 4).map((reason) => <span key={reason}>{reason}</span>)}
              </div>
            )}
            {expanded && (
              <div className="btc-transfer-news-match__detail">
                <NewsSignalOriginal signal={displaySignal} />
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

function inferredBtcAddressLabel(signal: Record<string, any>, target: WhaleTarget) {
  const text = `${String(signal.title ?? "")} ${String(signal.summary ?? "")} ${String(signal.behavior ?? "")}`;
  if (/blackrock|ibit|贝莱德/i.test(text)) return "贝莱德 / IBIT";
  return target.label || "BTC 地址";
}

function isIbitHistoryJobRunning(job: IbitHistorySyncJobStatus | null) {
  return job?.status === "pending" || job?.status === "running";
}

function useIbitHistorySyncJob(targetId: string, onResult?: (result: IbitHistorySyncResult) => void) {
  const queryClient = useQueryClient();
  const [job, setJob] = useState<IbitHistorySyncJobStatus | null>(null);
  const terminalJobRef = useRef("");
  const startMutation = useMutation({
    mutationFn: () => api.startIbitHistorySyncJob(targetId, { lookback_days: 30, max_news_items: 300 }),
    onSuccess: (nextJob) => {
      terminalJobRef.current = "";
      setJob(nextJob);
    },
    onError: (error) => {
      const now = new Date().toISOString();
      setJob({
        job_id: `local-${Date.now()}`,
        target_id: targetId,
        status: "failed",
        stage: "启动失败",
        message: readErrorMessage(error),
        progress: 100,
        current: 0,
        total: 100,
        started_at: now,
        updated_at: now,
        completed_at: now,
        result: null
      });
    }
  });
  const running = isIbitHistoryJobRunning(job);
  const jobId = job?.job_id ?? "";
  const jobQuery = useQuery({
    queryKey: ["ibit-history-sync-job", targetId, jobId],
    queryFn: () => api.ibitHistorySyncJob(targetId, jobId),
    enabled: Boolean(jobId && running && !jobId.startsWith("local-")),
    refetchInterval: running ? 1500 : false
  });
  useEffect(() => {
    const nextJob = jobQuery.data;
    if (!nextJob) return;
    setJob(nextJob);
    if ((nextJob.status === "completed" || nextJob.status === "failed") && terminalJobRef.current !== nextJob.job_id) {
      terminalJobRef.current = nextJob.job_id;
      if (nextJob.result) onResult?.(nextJob.result);
      void queryClient.invalidateQueries({ queryKey: ["whale", targetId] });
      void queryClient.invalidateQueries({ queryKey: ["whales"] });
      void queryClient.invalidateQueries({ queryKey: ["btc-large-transfer-stats"] });
      void queryClient.invalidateQueries({ queryKey: ["btc-large-transfers"] });
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    }
  }, [jobQuery.data, onResult, queryClient, targetId]);
  return {
    job,
    start: () => startMutation.mutate(),
    isRunning: running || startMutation.isPending,
    isStarting: startMutation.isPending
  };
}

function IbitHistoryProgress({ job, compact = false }: { job: IbitHistorySyncJobStatus; compact?: boolean }) {
  const progress = Math.max(0, Math.min(100, Number(job.progress ?? 0)));
  const statusLabel = job.status === "completed" ? "完成" : job.status === "failed" ? "失败" : job.status === "pending" ? "排队" : "回扫中";
  const countText = job.total > 0 ? `${job.current}/${job.total}` : "";
  return (
    <div className={compact ? "history-progress compact" : "history-progress"}>
      <div className="history-progress__head">
        <strong>{job.stage || "IBIT 历史回扫"}</strong>
        <span>{statusLabel} · {formatNumber(progress, 0)}%{countText ? ` · ${countText}` : ""}</span>
      </div>
      <div className="history-progress__bar" aria-label="IBIT history sync progress">
        <span style={{ width: `${progress}%` }} />
      </div>
      {job.message && <small>{job.message}</small>}
    </div>
  );
}

function WhaleDetailPage({ targetId, onBack, setNotice }: { targetId: string; onBack: () => void; setNotice: (notice: string) => void }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["whale", targetId], queryFn: () => api.whaleDetail(targetId) });
  const [tab, setTab] = useState<"basic" | "contracts" | "fills" | "spot" | "orders" | "ledger" | "history" | "flows" | "btcCluster" | "btcLargeTransfers" | "suspectedAddresses" | "newsSignals" | "events">("contracts");
  const [selectedIbitNewsId, setSelectedIbitNewsId] = useState("");
  const [historyResult, setHistoryResult] = useState<IbitHistorySyncResult | null>(null);
  const historySync = useIbitHistorySyncJob(targetId, setHistoryResult);
  const subscribeNewsAddress = useMutation({
    mutationFn: ({ address, label }: { address: string; label: string }) => api.confirmWhaleBtcAddress(targetId, address, "confirmed", label),
    onSuccess: (_result, variables) => {
      setNotice(`订阅成功：${shortText(variables.address, 10)} 已加入监控`);
      void queryClient.invalidateQueries({ queryKey: ["whale", targetId] });
      void queryClient.invalidateQueries({ queryKey: ["whales"] });
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    },
    onError: (error) => setNotice(`订阅失败：${readErrorMessage(error)}`)
  });
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
  const isBlackRockFree = isBlackRockTarget(target) || account.source === "blackrock_free";
  const activeTab = isBlackRockFree && !["basic", "flows", "btcCluster", "btcLargeTransfers", "suspectedAddresses", "newsSignals", "events"].includes(tab) ? "basic" : tab;
  const raw = data.snapshot?.raw ?? {};
  const blackrockFlow = raw.farside && typeof raw.farside === "object" ? raw.farside as Record<string, any> : {};
  const blackrockCluster = raw.btc_cluster && typeof raw.btc_cluster === "object" ? raw.btc_cluster as Record<string, any> : {};
  const ibitNewsSignals = raw.news_signals && typeof raw.news_signals === "object" ? raw.news_signals as Record<string, any> : {};
  const historyWorkflow = historyResult ?? (raw.history_workflow && typeof raw.history_workflow === "object" ? raw.history_workflow as IbitHistorySyncResult : null);
  const ibitSignalRows = Array.isArray(ibitNewsSignals.signals) ? ibitNewsSignals.signals as Record<string, any>[] : [];
  const suspectedAddresses = Array.isArray(ibitNewsSignals.suspected_addresses) ? ibitNewsSignals.suspected_addresses as Record<string, any>[] : [];
  const suspectedAddressPool = arr(ibitNewsSignals.suspected_address_pool ?? target.config.suspected_btc_addresses);
  const matchedAddressCount = newsMatchedAddressCount(ibitSignalRows, suspectedAddresses);
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
        <button className={activeTab === "basic" ? "active" : ""} onClick={() => setTab("basic")}>基本信息</button>
        {isBlackRockFree ? (
          <>
            <button className={activeTab === "flows" ? "active" : ""} onClick={() => setTab("flows")}>ETF资金流</button>
            <button className={activeTab === "btcCluster" ? "active" : ""} onClick={() => setTab("btcCluster")}>BTC地址簇</button>
            <button className={activeTab === "btcLargeTransfers" ? "active" : ""} onClick={() => setTab("btcLargeTransfers")}>链上大额底表</button>
            <button className={activeTab === "suspectedAddresses" ? "active" : ""} onClick={() => setTab("suspectedAddresses")}>疑似地址</button>
            <button className={activeTab === "newsSignals" ? "active" : ""} onClick={() => setTab("newsSignals")}>新闻线索</button>
            <button className={activeTab === "events" ? "active" : ""} onClick={() => setTab("events")}>最近动态</button>
          </>
        ) : (
          <>
            <button className={activeTab === "contracts" ? "active" : ""} onClick={() => setTab("contracts")}>合约 ({positions.length})</button>
            <button className={activeTab === "fills" ? "active" : ""} onClick={() => setTab("fills")}>最近成交 ({fills.length})</button>
            <button className={activeTab === "spot" ? "active" : ""} onClick={() => setTab("spot")}>现货/DeFi ({holdings.length + defiPositions.length})</button>
            <button className={activeTab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>当前委托 ({openOrders.length})</button>
            <button className={activeTab === "ledger" ? "active" : ""} onClick={() => setTab("ledger")}>资金流水 ({funding.length + ledgerUpdates.length})</button>
            <button className={activeTab === "history" ? "active" : ""} onClick={() => setTab("history")}>历史订单 ({historicalOrders.length})</button>
            <button className={activeTab === "events" ? "active" : ""} onClick={() => setTab("events")}>最近动态</button>
          </>
        )}
      </div>
      {activeTab === "basic" && (
        <Panel title="基本信息" dragHandle={false} action={data.updated_at ? <span className="pill">更新 {cnDate(data.updated_at)}</span> : undefined}>
          <div className="whale-summary-grid">
            {isBlackRockFree ? (
              <>
                <Metric label="IBIT官方净资产" value={money(account.blackrock_official_net_assets)} />
                <Metric label="估算BTC持仓" value={account.blackrock_official_estimated_btc == null ? "--" : `${formatNumber(account.blackrock_official_estimated_btc, 2)} BTC`} />
                <Metric label="官方BTC基准价" value={money(account.blackrock_official_benchmark_price)} />
                <Metric label="最新IBIT资金流" value={money(account.blackrock_last_flow_usd)} tone={Number(account.blackrock_last_flow_usd ?? 0) >= 0 ? "up" : "down"} />
                <Metric label="资金流日期" value={String(account.blackrock_last_flow_date ?? "--")} />
                <Metric label="已确认地址数" value={String(account.blackrock_btc_cluster_address_count ?? 0)} />
                <Metric label="疑似地址数" value={String(account.ibit_suspected_address_count ?? suspectedAddresses.length)} />
                <Metric label="底表新闻命中" value={String(account.ibit_btc_large_transfer_match_count ?? 0)} />
              </>
            ) : (
              <>
                <Metric label="合约名义价值" value={money(account.contract_notional)} />
                <Metric label="合约未实现盈亏" value={money(account.contract_pnl)} tone={Number(account.contract_pnl ?? 0) >= 0 ? "up" : "down"} />
                <Metric label="现货资产" value={money(account.spot_value ?? account.total_balance)} />
                <Metric label="DeFi 仓位" value={money(account.defi_value)} />
                <Metric label="可提现" value={money(account.withdrawable, { showZero: true })} />
                <Metric label="保证金占用" value={money(account.total_margin_used)} />
              </>
            )}
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
      {activeTab === "flows" && isBlackRockFree && (
        <Panel title="ETF资金流（非ETH链）" dragHandle={false}>
          <div className="whale-summary-grid">
            <Metric label="IBIT最新ETF净流入/流出" value={money(account.blackrock_last_flow_usd)} tone={Number(account.blackrock_last_flow_usd ?? 0) >= 0 ? "up" : "down"} />
            <Metric label="ETF资金流日期" value={String(account.blackrock_last_flow_date ?? "--")} />
            <Metric label="ETF净流入阈值" value={money(target.config.blackrock_flow_alert_min_usd ?? 50000000)} />
          </div>
          <div className="records compact">
            {blackrockFlow.values && typeof blackrockFlow.values === "object" ? Object.entries(blackrockFlow.values).map(([name, value]) => (
              <span key={name}>{name}: {typeof value === "number" ? `${formatNumber(value, 1)}M` : String(value)}</span>
            )) : <span className="empty">暂无 Farside ETF 资金流明细</span>}
          </div>
        </Panel>
      )}
      {activeTab === "btcCluster" && isBlackRockFree && <IbitBtcAddressPanel target={target} detail={data} mode="confirmed" setNotice={setNotice} />}
      {false && activeTab === "btcCluster" && isBlackRockFree && (
        <Panel title="BTC地址簇" dragHandle={false}>
          <div className="whale-summary-grid">
            <Metric label="已确认地址数" value={String(account.blackrock_btc_cluster_address_count ?? arr(target.config.btc_addresses).length)} />
            <Metric label="大额转出笔数" value={String(account.blackrock_btc_cluster_transfer_count ?? 0)} />
            <Metric label="逐笔操作数" value={String(account.blackrock_btc_cluster_operation_count ?? addressActivityCount(blackrockCluster.address_activity))} />
            <Metric label="转出阈值" value={`${formatNumber(target.config.blackrock_btc_transfer_min_btc ?? 1000, 0)} BTC`} />
          </div>
          <div className="records compact">
            {arr(blackrockCluster.addresses ?? target.config.btc_addresses).map((address) => <span key={address}>{address}</span>)}
            {!arr(blackrockCluster.addresses ?? target.config.btc_addresses).length && <span className="empty">未配置已确认 BTC 地址簇；只会监控官方日频和 ETF 资金流</span>}
          </div>
          <div className="records">
            {Array.isArray(blackrockCluster.transfers) && blackrockCluster.transfers.map((transfer: Record<string, any>) => (
              <span key={String(transfer.txid)}>
                {cnDateFromAny(transfer.timestamp ?? transfer.timestamp_ms)} · 转出 {formatNumber(transfer.amount_btc, 4)} BTC · {String(transfer.txid ?? "--")}
              </span>
            ))}
            {(!Array.isArray(blackrockCluster.transfers) || !blackrockCluster.transfers.length) && <span className="empty">暂无超过阈值的地址簇转出</span>}
          </div>
          <div className="records">
            {addressActivityRows(blackrockCluster.address_activity).map((operation) => (
              <span key={`${operation.address}-${operation.txid}`}>
                {cnDateFromAny(operation.timestamp ?? operation.timestamp_ms)} · {shortText(operation.address, 10)} · {String(operation.behavior ?? operation.direction ?? "操作")} {formatNumber(operation.amount_btc, 4)} BTC · 净额 {formatNumber(operation.net_btc, 4)} BTC · {shortText(operation.txid, 12)}
              </span>
            ))}
            {!addressActivityRows(blackrockCluster.address_activity).length && <span className="empty">暂无已确认地址的逐笔链上操作</span>}
          </div>
        </Panel>
      )}
      {activeTab === "btcLargeTransfers" && isBlackRockFree && (
        <IbitBtcLargeTransferPanel targetId={target.id} detail={data} setNotice={setNotice} />
      )}
      {activeTab === "suspectedAddresses" && isBlackRockFree && <IbitBtcAddressPanel target={target} detail={data} mode="suspected" setNotice={setNotice} />}
      {false && activeTab === "suspectedAddresses" && isBlackRockFree && (
        <Panel title="疑似地址" dragHandle={false}>
          <div className="whale-summary-grid">
            <Metric label="疑似地址数" value={String(suspectedAddresses.length)} />
            <Metric label="候选池地址" value={String(suspectedAddressPool.length)} />
            <Metric label="新闻线索数" value={String(account.ibit_news_candidate_count ?? arr(ibitNewsSignals.signals).length)} />
            <Metric label="提醒置信度" value={`${formatNumber((target.config.ibit_news_candidate_notify_min_confidence ?? 0.7) * 100, 0)}%`} />
          </div>
          <div className="records">
            {suspectedAddresses.map((item) => (
              <span key={String(item.address)}>
                {String(item.address)} · 置信度 {formatNumber(Number(item.confidence ?? 0) * 100, 0)}% · 线索 {Number(item.signal_count ?? (Array.isArray(item.signals) ? item.signals.length : 0))} 条
                {Array.isArray(item.latest_operations) && item.latest_operations[0] ? ` · 最新${String(item.latest_operations[0].behavior ?? "操作")} ${formatNumber(item.latest_operations[0].amount_btc, 4)} BTC` : ""}
                {Array.isArray(item.txids) && item.txids.length ? ` · txid ${item.txids.map((txid: unknown) => shortText(txid, 10)).join(", ")}` : ""}
                {Array.isArray(item.signals) && item.signals[0] ? ` · ${String(item.signals[0].behavior ?? "")} · ${String(item.signals[0].title ?? "")}` : ""}
                {Array.isArray(item.reasons) && item.reasons.length ? ` · 依据：${item.reasons.slice(0, 4).join("；")}` : ""}
              </span>
            ))}
            {!suspectedAddresses.length && <span className="empty">暂无疑似地址。系统会先采集 BTC/ETH 链上大额底表，再把新闻的时间、金额和方向与底表交易做相似匹配；也可以在后台补充候选地址池。</span>}
          </div>
        </Panel>
      )}
      {activeTab === "newsSignals" && isBlackRockFree && (
        <Panel
          title="新闻线索"
          dragHandle={false}
          action={<button disabled={historySync.isRunning} onClick={historySync.start}><RefreshCw size={16} /> {historySync.isRunning ? "回扫中" : "回扫30天"}</button>}
        >
          <div className="whale-summary-grid whale-summary-grid--compact news-signal-summary">
            <Metric label="线索数量" value={String(account.ibit_news_candidate_count ?? ibitSignalRows.length)} />
            <Metric label="新闻源数量" value={String(arr(ibitNewsSignals.feed_urls ?? target.config.ibit_news_rss_urls).length)} />
            <Metric label="匹配地址" value={String(matchedAddressCount)} />
            <Metric label="提醒置信度" value={`${formatNumber((target.config.ibit_news_candidate_notify_min_confidence ?? 0.6) * 100, 0)}%`} />
          </div>
          {historySync.job && <IbitHistoryProgress job={historySync.job} />}
          {historyWorkflow && (
            <div className="records compact history-workflow">
              <span>工作流: {historyWorkflow.ok ? "完成" : "失败"} · 回看 {historyWorkflow.lookback_days} 天 · 地址 {historyWorkflow.address_count} 个 · 账户底单 {historyWorkflow.account_operation_count} 条 · 新闻 {historyWorkflow.news_signal_count} 条 · 匹配地址 {historyWorkflow.matched_address_count} 个 · 底表命中 {historyWorkflow.large_transfer_match_count} 条</span>
              {historyWorkflow.message && <span>{historyWorkflow.message}</span>}
            </div>
          )}
          <div className="news-match-stack">
            {ibitSignalRows.map((signal) => {
              const key = newsSignalKey(signal);
              const matches = suspectedMatchesForSignal(suspectedAddresses, signal);
              const expanded = selectedIbitNewsId === key;
              const btcAmounts = Array.isArray(signal.btc_amounts) ? signal.btc_amounts : [];
              const ethAmounts = Array.isArray(signal.eth_amounts) ? signal.eth_amounts : [];
              const usdAmounts = Array.isArray(signal.usd_amounts) ? signal.usd_amounts : [];
              const txidCount = Array.isArray(signal.txids) ? signal.txids.length : 0;
              return (
                <div className={`news-match-card${expanded ? " expanded" : ""}`} key={key}>
                  <button className="news-match-card__button" onClick={() => setSelectedIbitNewsId(expanded ? "" : key)}>
                    <span className="news-match-card__main">
                      <span className="news-match-card__title-line">
                        <small>{cnDateFromAny(signal.published_at)}</small>
                        <strong>{String(signal.title ?? "IBIT 新闻线索")}</strong>
                      </span>
                      <span className="news-match-card__meta">
                        <span>置信度 {formatNumber(Number(signal.confidence ?? 0) * 100, 0)}%</span>
                        {matches.length ? <span>匹配地址 {matches.length} 个</span> : null}
                        {txidCount ? <span>txid {txidCount} 个</span> : null}
                        {btcAmounts.length ? <span>{btcAmounts.map((value: unknown) => `${formatNumber(value, 4)} BTC`).join(" / ")}</span> : null}
                        {ethAmounts.length ? <span>{ethAmounts.map((value: unknown) => `${formatNumber(value, 4)} ETH`).join(" / ")}</span> : null}
                        {usdAmounts.length ? <span>{usdAmounts.map((value: unknown) => money(value)).join(" / ")}</span> : null}
                      </span>
                    </span>
                    <em>{expanded ? "收起" : "查看匹配"}</em>
                  </button>
                  {expanded && (
                    <>
                      <NewsSignalOriginal signal={signal} />
                      <NewsSignalMatchList
                        matches={matches}
                        target={target}
                        canAdmin={Boolean(localStorage.getItem("adminToken"))}
                        subscribing={subscribeNewsAddress.isPending}
                        onSubscribe={(address, label) => {
                          if (isSubscribedBtcAddress(target, address)) {
                            setNotice(`这个 BTC 地址已经在监控中：${shortText(address, 10)}`);
                            return;
                          }
                          subscribeNewsAddress.mutate({ address, label });
                        }}
                      />
                    </>
                  )}
                  {false && expanded && (
                    <div className="records news-match-card__detail">
                      {matches.map(({ item, signal, confidence }) => {
                        const operation = signal.operation && typeof signal.operation === "object" ? signal.operation as Record<string, any> : Array.isArray(item.latest_operations) ? item.latest_operations[0] : undefined;
                        const reasons = Array.isArray(signal.match_reasons) && signal.match_reasons.length ? signal.match_reasons : Array.isArray(item.reasons) ? item.reasons : [];
                        return (
                          <span key={String(item.address)}>
                            {String(item.address)} · 置信度 {formatNumber(confidence * 100, 0)}%
                            {operation ? ` · ${cnDateFromAny(operation.timestamp ?? operation.timestamp_ms)} ${String(operation.behavior ?? operation.direction ?? "操作")} ${formatNumber(operation.amount_btc, 4)} BTC` : ""}
                            {Array.isArray(signal.txids) && signal.txids.length ? ` · txid ${signal.txids.map((txid: unknown) => shortText(txid, 10)).join(", ")}` : ""}
                            {reasons.length ? ` · 依据：${reasons.slice(0, 4).join("；")}` : ""}
                          </span>
                        );
                      })}
                      {!matches.length && <span className="empty">这条新闻暂无匹配地址</span>}
                    </div>
                  )}
                </div>
              );
            })}
            {!ibitSignalRows.length && <span className="empty">暂无新闻线索。启用 IBIT 新闻线索并配置 RSS 源后，系统会提取 BTC/ETH 金额、方向、txid 和地址，并与链上大额底表做疑似匹配。</span>}
          </div>
        </Panel>
      )}
      {activeTab === "contracts" && !isBlackRockFree && (
        <Panel title="持仓列表" dragHandle={false} action={<span className="pill">共 {positions.length} 个持仓</span>}>
          <div className="position-list">
            {positions.map((position, index) => <PositionCard key={index} position={position} />)}
            {!positions.length && <div className="empty-state">暂无合约持仓。启用巨鲸策略并配置完整 0x 地址后，Hyperliquid 会同步仓位价值、保证金、开仓均价、强平价格、资金费和盈亏。</div>}
          </div>
        </Panel>
      )}
      {activeTab === "spot" && !isBlackRockFree && (
        <Panel title="现货与 DeFi 仓位" dragHandle={false} action={<span className="pill">{holdings.length} 现货 / {defiPositions.length} 协议</span>}>
          <div className="asset-list">
            {holdings.map((holding, index) => <AssetCard key={`holding-${index}`} item={holding} />)}
            {defiPositions.map((item, index) => <AssetCard key={`defi-${index}`} item={item} protocol />)}
            {!holdings.length && !defiPositions.length && <div className="empty-state">暂无多链资产数据。配置 DeBank AccessKey 后会同步现货资产和 DeFi 协议仓位。</div>}
          </div>
        </Panel>
      )}
      {activeTab === "fills" && !isBlackRockFree && (
        <Panel title="最近成交" dragHandle={false} action={<span className="pill">共 {fills.length} 笔</span>}>
          <div className="whale-table">
            {fills.map((fill, index) => <WhaleFillRow key={`${fill.hash ?? fill.trade_id ?? index}`} fill={fill} isLarge={isLargeTrade(fill, target.config)} />)}
            {!fills.length && <span className="empty">暂无最近成交</span>}
          </div>
        </Panel>
      )}
      {activeTab === "orders" && !isBlackRockFree && (
        <Panel title="当前委托" dragHandle={false}>
          <div className="records">
            {openOrders.map((order, index) => <OrderRow key={index} order={order} />)}
            {!openOrders.length && <span className="empty">暂无当前委托</span>}
          </div>
        </Panel>
      )}
      {activeTab === "ledger" && !isBlackRockFree && (
        <Panel title="资金流水与资金费" dragHandle={false}>
          <div className="whale-table">
            {funding.map((item, index) => <FundingRow key={`funding-${index}`} item={item} />)}
            {ledgerUpdates.map((item, index) => <LedgerRow key={`ledger-${index}`} item={item} />)}
            {!funding.length && !ledgerUpdates.length && <span className="empty">暂无资金费或出入金流水</span>}
          </div>
        </Panel>
      )}
      {activeTab === "history" && !isBlackRockFree && (
        <Panel title="历史订单" dragHandle={false}>
          <div className="whale-table">
            {historicalOrders.map((order, index) => <OrderRow key={index} order={order} />)}
            {!historicalOrders.length && <span className="empty">暂无历史订单</span>}
          </div>
        </Panel>
      )}
      {activeTab === "events" && (
        <Panel title="操作动态" dragHandle={false}>
          <div className="records">
            {data.recent_events.map((event) => {
              const currentPosition = whaleEventCurrentPosition(event);
              return (
                <span key={String(event.id)}>
                  {cnDate(event.occurred_at_utc)} · {String(event.summary)}
                  {currentPosition ? ` · 当前仓位: ${currentPosition}` : ""}
                </span>
              );
            })}
            {!data.recent_events.length && <span className="empty">暂无操作动态</span>}
          </div>
        </Panel>
      )}
    </div>
  );
}

function IbitBtcLargeTransferPanel({
  targetId,
  detail,
  setNotice
}: {
  targetId: string;
  detail?: WhaleDetail;
  setNotice: (notice: string) => void;
}) {
  const queryClient = useQueryClient();
  const [minBtc, setMinBtc] = useState(0);
  const [query, setQuery] = useState("");
  const [matchedOnly, setMatchedOnly] = useState(false);
  const [selectedTxid, setSelectedTxid] = useState("");
  const [historyStart, setHistoryStart] = useState("");
  const [historyEnd, setHistoryEnd] = useState("");
  const [historyMaxBlocks, setHistoryMaxBlocks] = useState(24);
  const [rescanSummary, setRescanSummary] = useState("");
  const canAdmin = Boolean(localStorage.getItem("adminToken"));
  const subscribedAddresses = useMemo(() => subscribedBtcAddressSet(detail?.target), [detail?.target]);
  const statsQuery = useQuery({ queryKey: ["btc-large-transfer-stats"], queryFn: api.btcLargeTransferStats, refetchInterval: 30_000 });
  const transfersQuery = useQuery({
    queryKey: ["btc-large-transfers", minBtc, query, matchedOnly],
    queryFn: () => api.btcLargeTransfers({ limit: 50, min_btc: minBtc, query, matched_only: matchedOnly }),
    refetchInterval: 30_000
  });
  const detailQuery = useQuery({
    queryKey: ["btc-large-transfer", selectedTxid],
    queryFn: () => api.btcLargeTransfer(selectedTxid),
    enabled: Boolean(selectedTxid)
  });
  const rescan = useMutation({
    mutationFn: (payload: number | { blocks?: number | null; start_utc?: string; end_utc?: string; max_blocks?: number }) => api.rescanBtcLargeTransfers(payload),
    onSuccess: (result) => {
      setRescanSummary(`已扫描 BTC ${result.scanned_blocks} 块，新增 ${result.inserted} 笔；ETH ${result.scanned_eth_blocks ?? 0} 块，新增 ${result.inserted_eth ?? 0} 笔${result.start_height && result.end_height ? `，BTC 高度 ${result.start_height}-${result.end_height}` : ""}${result.message ? `，${result.message}` : ""}`);
      void queryClient.invalidateQueries({ queryKey: ["btc-large-transfer-stats"] });
      void queryClient.invalidateQueries({ queryKey: ["btc-large-transfers"] });
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    }
  });
  const ibitHistorySync = useIbitHistorySyncJob(targetId, (result) => {
    setRescanSummary(`IBIT 30天回扫完成：底单 ${result.account_operation_count} 条，ETH底表 ${result.eth_large_transfer_inserted ?? 0} 条，新闻 ${result.news_signal_count} 条，匹配地址 ${result.matched_address_count} 个${result.message ? `，${result.message}` : ""}`);
  });
  const confirmAddress = useMutation({
    mutationFn: ({ address, role }: { address: string; role: "candidate" | "confirmed" }) => api.confirmWhaleBtcAddress(targetId, address, role),
    onSuccess: (_result, variables) => {
      setNotice(`订阅成功：${shortText(variables.address, 10)} 已加入监控`);
      void queryClient.invalidateQueries({ queryKey: ["whale", targetId] });
      void queryClient.invalidateQueries({ queryKey: ["whales"] });
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
    },
    onError: (error) => setNotice(`订阅失败：${readErrorMessage(error)}`)
  });
  const subscribeAddress = (address: string, role: "candidate" | "confirmed") => {
    if (role === "confirmed" && subscribedAddresses.has(normalizeAddress(address))) {
      setNotice(`这个 BTC 地址已经在监控中：${shortText(address, 10)}`);
      return;
    }
    confirmAddress.mutate({ address, role });
  };
  const stats = statsQuery.data;
  const transfers = transfersQuery.data?.items ?? [];
  const fallbackOperations = useMemo(() => {
    const raw = detail?.snapshot?.raw ?? {};
    const cluster = raw.btc_cluster && typeof raw.btc_cluster === "object" ? raw.btc_cluster as Record<string, any> : {};
    const queryText = query.trim().toLowerCase();
    return addressActivityRows(cluster.address_activity)
      .filter((operation) => Number(operation.amount_btc ?? 0) >= minBtc)
      .filter((operation) => !queryText || String(operation.txid ?? "").toLowerCase().includes(queryText) || String(operation.address ?? "").toLowerCase().includes(queryText));
  }, [detail, minBtc, query]);
  const selectedOperation = selectedTxid ? fallbackOperations.find((operation) => String(operation.txid ?? "") === selectedTxid) : undefined;
  const selectedTransfer = detailQuery.data ?? transfers.find((item) => item.txid === selectedTxid);
  const minEthThreshold = stats?.min_eth ?? 5000;
  return (
    <Panel
      title="链上大额底表"
      dragHandle={false}
      action={
        <div className="panel-actions">
          {canAdmin && <button onClick={ibitHistorySync.start} disabled={ibitHistorySync.isRunning}><RefreshCw size={14} /> {ibitHistorySync.isRunning ? "回扫中" : "回扫30天IBIT"}</button>}
          <button onClick={() => transfersQuery.refetch()} disabled={transfersQuery.isFetching}><RefreshCw size={14} /> 刷新</button>
          {canAdmin && <button onClick={() => rescan.mutate(3)} disabled={rescan.isPending}><Database size={14} /> 补扫3块</button>}
        </div>
      }
    >
      <div className="whale-summary-grid">
        <Metric label="已记录交易" value={String(stats?.total ?? 0)} />
        <Metric label="今日新增" value={String(stats?.today_count ?? 0)} />
        <Metric label="新闻命中" value={String(stats?.matched_count ?? 0)} />
        <Metric label="BTC最新高度" value={stats?.latest_scanned_height ? String(stats.latest_scanned_height) : "--"} />
        <Metric label="ETH最新高度" value={stats?.latest_eth_scanned_height ? String(stats.latest_eth_scanned_height) : "--"} />
        <Metric label="当前阈值" value={`${formatNumber(stats?.min_btc ?? minBtc, 0)} BTC / ${formatNumber(minEthThreshold, 0)} ETH`} />
      </div>
      {ibitHistorySync.job && <IbitHistoryProgress job={ibitHistorySync.job} compact />}
      {canAdmin && (
        <div className="btc-history-rescan">
          <label>
            <span>历史开始</span>
            <input type="datetime-local" value={historyStart} onChange={(event) => setHistoryStart(event.target.value)} />
          </label>
          <label>
            <span>历史结束</span>
            <input type="datetime-local" value={historyEnd} onChange={(event) => setHistoryEnd(event.target.value)} />
          </label>
          <label>
            <span>最多区块</span>
            <input type="number" min={1} max={288} value={historyMaxBlocks} onChange={(event) => setHistoryMaxBlocks(Math.max(1, Math.min(288, Number(event.target.value) || 1)))} />
          </label>
          <button
            disabled={rescan.isPending || !historyStart || !historyEnd}
            onClick={() => rescan.mutate({ blocks: null, start_utc: localDateTimeToIso(historyStart), end_utc: localDateTimeToIso(historyEnd), max_blocks: historyMaxBlocks })}
          >
            <Database size={14} /> 历史回扫
          </button>
          {rescanSummary && <small>{rescanSummary}</small>}
        </div>
      )}
      <div className="btc-transfer-toolbar">
        <label>
          <span>最小数量</span>
          <input type="number" value={minBtc} min={0} onChange={(event) => setMinBtc(Math.max(0, Number(event.target.value) || 0))} />
        </label>
        <label>
          <span>txid / 地址 / 币种</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 txid、地址、BTC 或 ETH" />
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={matchedOnly} onChange={(event) => setMatchedOnly(event.target.checked)} />
          <span>只看新闻命中</span>
        </label>
      </div>
      <div className="btc-transfer-table">
        {transfers.map((transfer) => {
          const expanded = selectedTxid === transfer.txid;
          const detail = expanded ? (detailQuery.data ?? transfer) : transfer;
          return (
            <div className="btc-transfer-table__item" key={transfer.txid}>
              <button
                className={expanded ? "btc-transfer-table__row active" : "btc-transfer-table__row"}
                onClick={() => setSelectedTxid(expanded ? "" : transfer.txid)}
              >
                <strong>{transferAmountText(transfer)}</strong>
                <span>{cnDate(transfer.block_time_utc)}</span>
                <span>高度 {transfer.block_height}</span>
                <span>输入 {transfer.input_addresses.length} / 输出 {transfer.output_addresses.length}</span>
                <span title={transfer.txid}>{shortText(transfer.txid, 16)}</span>
                <span>{transfer.match_count ? `新闻命中 ${transfer.match_count}` : "未命中新闻"}</span>
                <em>{expanded ? "收起" : "详情"}</em>
              </button>
              {expanded && (
                <div className="btc-transfer-table__detail">
                  <BtcTransferDetail
                    transfer={detail}
                    canAdmin={canAdmin}
                    confirming={confirmAddress.isPending}
                    subscribedAddresses={subscribedAddresses}
                    onConfirm={subscribeAddress}
                  />
                </div>
              )}
            </div>
          );
        })}
        {!transfers.length && fallbackOperations.map((operation) => {
          const txid = String(operation.txid ?? "");
          const expanded = selectedTxid === txid;
          return (
            <div className="btc-transfer-table__item" key={`${operation.address}-${operation.txid}-${operation.direction}`}>
              <button
                className={expanded ? "btc-transfer-table__row active" : "btc-transfer-table__row"}
                onClick={() => setSelectedTxid(expanded ? "" : txid)}
              >
                <strong>{formatNumber(operation.amount_btc, 4)} BTC</strong>
                <span>{cnDateFromAny(operation.timestamp ?? operation.timestamp_ms)}</span>
                <span>{shortText(operation.address, 12)}</span>
                <span>{String(operation.behavior ?? operation.direction ?? "--")}</span>
                <span title={txid}>{shortText(txid, 16)}</span>
                <span>净额 {formatNumber(operation.net_btc, 4)} BTC</span>
                <em>{expanded ? "收起" : "详情"}</em>
              </button>
              {expanded && (
                <div className="btc-transfer-table__detail">
                  <BtcAddressOperationDetail operation={operation} />
                </div>
              )}
            </div>
          );
        })}
        {transfersQuery.isLoading && <span className="empty">正在加载链上大额底表…</span>}
        {!transfersQuery.isLoading && !transfers.length && !fallbackOperations.length && <span className="empty">暂无大额交易记录。启用巨鲸策略后，系统会按区块增量采集超过阈值的 BTC/ETH 已确认交易。</span>}
      </div>
    </Panel>
  );
}

function transferAsset(transfer: Partial<BtcLargeTransfer> | Record<string, any>) {
  return String(transfer.asset ?? "BTC").toUpperCase();
}

function transferAmount(transfer: Partial<BtcLargeTransfer> | Record<string, any>) {
  const value = Number(transfer.amount ?? 0);
  if (Number.isFinite(value) && value > 0) return value;
  return Number(transfer.amount_btc ?? 0);
}

function transferAmountText(transfer: Partial<BtcLargeTransfer> | Record<string, any>, digits = 4) {
  return `${formatNumber(transferAmount(transfer), digits)} ${transferAsset(transfer)}`;
}

function transferExplorerLabel(transfer: Partial<BtcLargeTransfer> | Record<string, any>) {
  const chain = String(transfer.chain ?? "").toLowerCase();
  return chain === "eth" ? "Etherscan" : "Blockstream";
}

function addressItemValue(item: Record<string, any>, asset: string) {
  const exact = Number(item[`value_${asset.toLowerCase()}`] ?? 0);
  if (Number.isFinite(exact) && exact > 0) return exact;
  const generic = Number(item.value ?? item.amount ?? 0);
  if (Number.isFinite(generic) && generic > 0) return generic;
  return Number(item.value_btc ?? 0);
}

function BtcAddressOperationDetail({ operation }: { operation: Record<string, any> }) {
  return (
    <div className="btc-transfer-detail__inner">
      <div className="btc-transfer-detail__head">
        <strong>{formatNumber(operation.amount_btc, 4)} BTC</strong>
        {operation.source_url && <a href={String(operation.source_url)} target="_blank" rel="noreferrer">Blockstream</a>}
      </div>
      <small>{String(operation.txid ?? "--")}</small>
      <div className="btc-address-detail-grid">
        <Metric label="地址" value={shortText(operation.address, 12)} />
        <Metric label="方向" value={String(operation.behavior ?? operation.direction ?? "--")} />
        <Metric label="净额" value={`${formatNumber(operation.net_btc, 4)} BTC`} />
        <Metric label="时间" value={cnDateFromAny(operation.timestamp ?? operation.timestamp_ms)} />
        <Metric label="确认状态" value={operation.confirmed ? "已确认" : "未确认"} />
        <Metric label="对手方" value={btcCounterpartyText(operation) || "--"} />
      </div>
      <BtcAddressOperationRow operation={operation} />
    </div>
  );
}

function BtcTransferDetail({
  transfer,
  canAdmin,
  confirming,
  subscribedAddresses,
  onConfirm
}: {
  transfer: BtcLargeTransfer;
  canAdmin: boolean;
  confirming: boolean;
  subscribedAddresses: ReadonlySet<string>;
  onConfirm: (address: string, role: "candidate" | "confirmed") => void;
}) {
  const topInputs = transfer.input_addresses.slice(0, 8);
  const topOutputs = transfer.output_addresses.slice(0, 8);
  const asset = transferAsset(transfer);
  const canSubscribeBtc = canAdmin && asset === "BTC";
  const subscribeButtonText = (address: string) => subscribedAddresses.has(normalizeAddress(address)) ? "已订阅" : "订阅监控";
  return (
    <div className="btc-transfer-detail__inner">
      <div className="btc-transfer-detail__head">
        <strong>{transferAmountText(transfer)}</strong>
        <a href={transfer.source_url} target="_blank" rel="noreferrer">{transferExplorerLabel(transfer)}</a>
      </div>
      <small>{transfer.txid}</small>
      <p className="btc-transfer-detail__hint">输入地址是这笔交易的资金来源，输出地址是接收方或找零地址。BTC 地址可加入 IBIT 地址簇持续监控；ETH 地址当前进入链上大额底表和新闻匹配，暂不混用 BTC 地址订阅接口。</p>
      <div className="btc-address-columns">
        <section>
          <b>输入地址</b>
          {topInputs.map((item) => (
            <div className="btc-address-row" key={String(item.address)}>
              <span className="btc-address-row__value">{String(item.address)} · {formatNumber(addressItemValue(item, asset), 4)} {asset}</span>
              {canSubscribeBtc && (
                <span className="btc-address-row__actions">
                  <button disabled={confirming} onClick={() => onConfirm(String(item.address), "confirmed")}>{subscribeButtonText(String(item.address))}</button>
                </span>
              )}
            </div>
          ))}
          {!topInputs.length && <span className="empty">暂无输入地址</span>}
        </section>
        <section>
          <b>输出地址</b>
          {topOutputs.map((item) => (
            <div className="btc-address-row" key={String(item.address)}>
              <span className="btc-address-row__value">{String(item.address)} · {formatNumber(addressItemValue(item, asset), 4)} {asset}</span>
              {canSubscribeBtc && (
                <span className="btc-address-row__actions">
                  <button disabled={confirming} onClick={() => onConfirm(String(item.address), "confirmed")}>{subscribeButtonText(String(item.address))}</button>
                </span>
              )}
            </div>
          ))}
          {!topOutputs.length && <span className="empty">暂无输出地址</span>}
        </section>
      </div>
      <BtcTransferNewsMatches matches={transfer.matches} />
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
  const directionLabel = whaleFillDirectionLabel(fill);
  const priceLabel = whaleFillPriceLabel(fill);
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
  const directionLabel = whaleFillDirectionLabel(fill);
  const priceLabel = whaleFillPriceLabel(fill);
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

function whaleFillDirectionLabel(fill: Record<string, any>) {
  const label = String(fill.direction_label ?? "").trim();
  const direction = String(fill.direction ?? label).trim();
  const normalized = direction.toLowerCase().replace(/_/g, " ");
  if (normalized.includes("liquidated")) {
    const margin = normalized.includes("cross") ? "全仓" : normalized.includes("isolated") ? "逐仓" : "";
    const side = normalized.includes("long") ? "多单" : normalized.includes("short") ? "空单" : "";
    return margin || side ? `强平${margin}${side}` : "强平";
  }
  return label || direction || "--";
}

function whaleFillPriceLabel(fill: Record<string, any>) {
  const label = String(fill.price_label ?? "").trim();
  if (label) return label;
  const normalized = String(fill.direction ?? fill.direction_label ?? "").trim().toLowerCase().replace(/_/g, " ");
  if (normalized.includes("liquidated")) return "强平价格";
  if (normalized.includes("close")) return "平仓价格";
  if (normalized.includes("open")) return "开仓价格";
  return "成交价格";
}

function cnDateFromAny(value: unknown) {
  if (value == null || value === "") return "暂无";
  if (typeof value === "number") return cnDate(new Date(value).toISOString());
  const asNumber = Number(value);
  if (Number.isFinite(asNumber) && String(value).length >= 10) return cnDate(new Date(asNumber).toISOString());
  return cnDate(String(value));
}

function localDateTimeToIso(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function whaleEventCurrentPosition(event: Record<string, any>) {
  const payload = event.payload && typeof event.payload === "object" ? event.payload : {};
  const label = typeof payload.current_position_label === "string" ? payload.current_position_label.trim() : "";
  if (label) return label;
  const position = payload.position && typeof payload.position === "object" ? payload.position : null;
  if (!position) return "";
  const coin = String(position.coin ?? position.symbol ?? "").split("-", 1)[0] || "--";
  if (event.action_type === "close_position") return `0 ${coin}`;
  const signedSize = Number(position.signed_size);
  if (Number.isFinite(signedSize)) return signedSize === 0 ? `0 ${coin}` : `${Math.abs(signedSize).toLocaleString("zh-CN")} ${coin} ${signedSize > 0 ? "多" : "空"}`;
  const size = Number(position.size);
  if (!Number.isFinite(size) || size <= 0) return "";
  const side = String(position.side ?? "");
  const sideLabel = side.includes("空") ? "空" : side.includes("多") ? "多" : side;
  return `${size.toLocaleString("zh-CN")} ${coin} ${sideLabel}`.trim();
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
  const strategiesRef = useRef(strategies);
  const dirtyStrategyIdsRef = useRef<Set<string>>(new Set());
  const chartModuleIndex = modules.findIndex((module) => module.id === "charts");
  const chartModule = chartModuleIndex >= 0 ? modules[chartModuleIndex] : undefined;

  useEffect(() => {
    setSymbols(data.symbols);
    setStrategies((current) => {
      if (!dirtyStrategyIdsRef.current.size) return data.strategies as MutableStrategy[];
      const currentById = new Map(current.map((strategy) => [strategy.id, strategy]));
      return (data.strategies as MutableStrategy[]).map((strategy) => (
        dirtyStrategyIdsRef.current.has(strategy.id) ? currentById.get(strategy.id) ?? strategy : strategy
      ));
    });
    setModules(data.modules);
  }, [data]);
  useEffect(() => {
    strategiesRef.current = strategies;
  }, [strategies]);

  const saveSymbols = useMutation({
    mutationFn: api.saveSymbols,
    onSuccess: () => done(queryClient, setNotice, "币种已保存"),
    onError: (error) => setNotice(`币种保存失败：${readErrorMessage(error)}`)
  });
  const saveStrategy = useMutation({
    mutationFn: api.saveStrategy,
    onSuccess: (saved) => {
      dirtyStrategyIdsRef.current.delete(saved.id);
      setStrategies((current) => current.map((strategy) => strategy.id === saved.id ? saved as MutableStrategy : strategy));
      done(queryClient, setNotice, "策略已保存");
    },
    onError: (error) => setNotice(`策略保存失败：${readErrorMessage(error)}`)
  });
  const saveModules = useMutation({
    mutationFn: api.saveModules,
    onSuccess: () => done(queryClient, setNotice, "模块显示已保存"),
    onError: (error) => setNotice(`模块显示保存失败：${readErrorMessage(error)}`)
  });
  const notifiersQuery = useQuery({ queryKey: ["notifiers"], queryFn: api.notifiers });
  const notifierWhalesQuery = useQuery({ queryKey: ["whales"], queryFn: api.whales });
  const notifierWhaleTargets = notifierWhalesQuery.data ?? [];
  const boundWhaleNotifierId = String(strategies.find((strategy) => strategy.id === "whale")?.notifier_id ?? "").trim();
  const boundWhaleNotifier = boundWhaleNotifierId ? notifiers.find((notifier) => notifier.id === boundWhaleNotifierId) : undefined;
  const boundWhaleNotifierLabel = boundWhaleNotifierId
    ? `${boundWhaleNotifier?.name || boundWhaleNotifierId} (${boundWhaleNotifierId})`
    : "";
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
                <div className={group.whaleTargets ? "strategy-editors strategy-editors--whale" : "strategy-editors"}>
                  {groupStrategies.map(({ strategy, index }) => (
                    <StrategyEditor
                      key={strategy.id}
                      strategy={strategy}
                      notifiers={notifiers}
                      whaleTargets={notifierWhaleTargets}
                      setNotice={setNotice}
                      onChange={(next) => {
                        dirtyStrategyIdsRef.current.add(next.id);
                        updateArray(strategies, setStrategies, index, next);
                      }}
                      onSave={() => {
                        const latest = strategiesRef.current[index] ?? strategy;
                        saveStrategy.mutate(latest);
                      }}
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
              <div className="notifier-row__actions">
                <button disabled={saveNotifiers.isPending || testNotifier.isPending || !notifier.id.trim()} onClick={() => testNotifierWithCurrentConfig(notifier)}><TestTube2 size={16} /> 测试</button>
                <button className="danger-button" onClick={() => setNotifiers(notifiers.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={16} /> 删除</button>
              </div>
              {notifier.type === "feishu" && (
                <NotifierWhaleTargetPicker
                  targets={notifierWhaleTargets}
                  value={arr(notifier.config?.whale_target_ids)}
                  btcValue={arr(notifier.config?.whale_btc_addresses)}
                  isWhaleBound={notifier.id.trim() !== "" && notifier.id.trim() === boundWhaleNotifierId}
                  boundNotifierLabel={boundWhaleNotifierLabel}
                  onChange={(whale_target_ids) => updateArray(notifiers, setNotifiers, index, { config: { ...(notifier.config ?? {}), whale_target_ids } })}
                  onBtcChange={(whale_btc_addresses) => updateArray(notifiers, setNotifiers, index, { config: { ...(notifier.config ?? {}), whale_btc_addresses } })}
                />
              )}
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

function NotifierWhaleTargetPicker({
  targets,
  value,
  btcValue,
  isWhaleBound,
  boundNotifierLabel,
  onChange,
  onBtcChange
}: {
  targets: WhaleTarget[];
  value: string[];
  btcValue: string[];
  isWhaleBound: boolean;
  boundNotifierLabel: string;
  onChange: (value: string[]) => void;
  onBtcChange: (value: string[]) => void;
}) {
  const [filter, setFilter] = useState("");
  const selected = new Set(value);
  const selectedBtc = new Set(btcValue.map((item) => item.toLowerCase()));
  const labelsByAddress = new Map<string, string>();
  targets.forEach((target) => {
    const labels = target.config?.btc_address_labels && typeof target.config.btc_address_labels === "object" ? target.config.btc_address_labels as Record<string, any> : {};
    configuredBtcAddresses(target).forEach((address) => labelsByAddress.set(address.toLowerCase(), `${btcAddressLabel(address, labels, target.label)} · ${shortText(address, 8)}`));
  });
  const selectedTargetLabels = targets.filter((target) => selected.has(target.id)).map((target) => target.label);
  const selectedBtcLabels = btcValue.map((address) => labelsByAddress.get(address.toLowerCase()) ?? shortText(address, 8));
  const selectedCount = selectedTargetLabels.length + selectedBtcLabels.length;
  const summary = selectedTargetLabels.length || selectedBtcLabels.length
    ? [...selectedTargetLabels, ...selectedBtcLabels].slice(0, 3).join("，") + (selectedTargetLabels.length + selectedBtcLabels.length > 3 ? ` 等 ${selectedTargetLabels.length + selectedBtcLabels.length} 项` : "")
    : "全部账户";
  const statusText = isWhaleBound
    ? (selectedCount ? "已绑定到巨鲸/IBIT策略，按当前账户范围发送提醒。" : "已绑定到巨鲸/IBIT策略，未选择账户时会接收全部巨鲸/IBIT提醒。")
    : (boundNotifierLabel ? `未绑定到巨鲸/IBIT策略，不会接收提醒；当前绑定机器人是 ${boundNotifierLabel}。` : "未绑定到巨鲸/IBIT策略，不会接收巨鲸/IBIT提醒。");
  const filterText = filter.trim().toLowerCase();
  const visibleTargets = targets.filter((target) => {
    if (!filterText) return true;
    const btcAddresses = configuredBtcAddresses(target);
    const labelText = `${target.label} ${target.id} ${target.address_or_subject} ${btcAddresses.join(" ")}`.toLowerCase();
    return labelText.includes(filterText);
  });
  const toggle = (id: string) => {
    if (selected.has(id)) {
      onChange(value.filter((item) => item !== id));
      return;
    }
    onChange([...value, id]);
  };
  const toggleBtc = (address: string) => {
    const key = address.toLowerCase();
    if (selectedBtc.has(key)) {
      onBtcChange(btcValue.filter((item) => item.toLowerCase() !== key));
      return;
    }
    onBtcChange([...btcValue, address]);
  };
  return (
    <div className={`notifier-target-filter ${isWhaleBound ? "notifier-target-filter--active" : "notifier-target-filter--inactive"}`}>
      <div className="notifier-target-filter__label">
        <span>巨鲸/IBIT提醒账户</span>
        <small>
          {isWhaleBound
            ? (value.length || btcValue.length ? `${value.length} 个账户 / ${btcValue.length} 条 BTC 链` : "未选择时接收全部")
            : (value.length || btcValue.length ? "已配置范围，绑定后生效" : "未绑定时不接收")}
        </small>
      </div>
      <div className={`notifier-scope-state ${isWhaleBound ? "notifier-scope-state--active" : "notifier-scope-state--inactive"}`}>
        <span>{isWhaleBound ? "生效中" : "未绑定"}</span>
        <small>{statusText}</small>
      </div>
      <details className="notifier-scope-select">
        <summary>
          <span>{summary}</span>
          <em>配置范围</em>
        </summary>
        <div className="notifier-scope-panel">
          <div className="notifier-scope-toolbar">
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索账户或 BTC 地址" />
            <button type="button" onClick={() => { onChange([]); onBtcChange([]); }}>接收全部</button>
          </div>
          <div className="notifier-scope-list">
            {visibleTargets.map((target) => {
              const btcAddresses = configuredBtcAddresses(target);
              const labels = target.config?.btc_address_labels && typeof target.config.btc_address_labels === "object" ? target.config.btc_address_labels as Record<string, any> : {};
              const targetSelected = selected.has(target.id);
              return (
                <div className="notifier-scope-item" key={target.id}>
                  <label className="notifier-scope-item__main">
                    <input type="checkbox" checked={targetSelected} onChange={() => toggle(target.id)} />
                    <span>
                      <strong>{target.label}</strong>
                      <small>{isBlackRockTarget(target) ? "IBIT 免费监控" : shortText(target.address_or_subject, 12)}</small>
                    </span>
                  </label>
                  {isBlackRockTarget(target) && (
                    <div className="notifier-scope-btc-list">
                      {btcAddresses.map((address) => (
                        <label key={address}>
                          <input type="checkbox" checked={selectedBtc.has(address.toLowerCase())} onChange={() => toggleBtc(address)} />
                          <span>{btcAddressLabel(address, labels, target.label)}</span>
                          <small>{shortText(address, 12)}</small>
                        </label>
                      ))}
                      {!btcAddresses.length && <small className="empty">暂无已订阅 BTC 地址；先到 IBIT 地址管理里添加。</small>}
                    </div>
                  )}
                </div>
              );
            })}
            {!visibleTargets.length && <span className="empty">{targets.length ? "没有匹配的账户" : "暂无关注对象"}</span>}
          </div>
        </div>
      </details>
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
          {drafts.map((target, index) => {
            const isBlackRockFree = String(target.config.provider ?? "") === "blackrock_free_monitor";
            if (isBlackRockFree) return null;
            return (
            <div className={`whale-target-row ${isBlackRockFree ? "whale-target-row--ibit" : ""}`} key={`${target.id ?? "new"}-${index}`}>
              <Switch checked={target.enabled} onChange={(enabled) => updateArray(drafts, setDrafts, index, { enabled })} />
              <input value={target.label} onChange={(event) => updateArray(drafts, setDrafts, index, { label: event.target.value })} placeholder="名称，例如 麻吉大哥" />
              <input value={target.address_or_subject} onChange={(event) => updateArray(drafts, setDrafts, index, { address_or_subject: event.target.value })} placeholder="主地址（必填）" />
              <WhaleTagSelector value={whaleTags(target.config.tags)} onChange={(tags) => updateArray(drafts, setDrafts, index, { config: { ...target.config, tags } })} />
              <input value={String(target.config.source_url ?? "")} onChange={(event) => updateArray(drafts, setDrafts, index, { config: { ...target.config, source_url: event.target.value } })} placeholder="来源链接（选填，不影响监控）" />
              <div className="whale-target-row__actions">
                <button disabled={saveTarget.isPending || !target.label.trim() || !target.address_or_subject.trim()} onClick={() => saveTarget.mutate(target)}><Save size={16} /> 保存</button>
                <button className="danger-button" disabled={deleteTarget.isPending} onClick={() => removeTarget(target, index)}><Trash2 size={16} /> 删除</button>
              </div>
            </div>
            );
          })}
          {!drafts.some((target) => String(target.config.provider ?? "") !== "blackrock_free_monitor") && <span className="empty">暂无普通巨鲸关注对象。IBIT 地址请在右侧 IBIT 免费监控里管理。</span>}
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
  whaleTargets,
  setNotice,
  onChange,
  onSave,
  disabled
}: {
  strategy: MutableStrategy;
  notifiers: NotifierTarget[];
  whaleTargets: WhaleTarget[];
  setNotice: (notice: string) => void;
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
  if (strategy.id === "whale") {
    return (
      <WhaleStrategyEditor
        strategy={strategy}
        notifiers={notifiers}
        whaleTargets={whaleTargets}
        setNotice={setNotice}
        onChange={onChange}
        onSave={onSave}
        disabled={disabled}
      />
    );
  }
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
            <TextField label="白宫来源页" value={String(config.whitehouse_gallery_url ?? "")} onChange={(whitehouse_gallery_url) => setConfig({ whitehouse_gallery_url })} />
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
            <Switch checked={Boolean(config.etherscan_enabled)} onChange={(etherscan_enabled) => setConfig({ etherscan_enabled })} label="Etherscan ETH底表" />
            <TextField label="Etherscan API" value={String(config.etherscan_api_url ?? "https://api.etherscan.io/v2/api")} onChange={(etherscan_api_url) => setConfig({ etherscan_api_url })} />
            <TextField label="Etherscan API Key" value={String(config.etherscan_api_key ?? "")} onChange={(etherscan_api_key) => setConfig({ etherscan_api_key })} />
            <TextField label="Etherscan Chain ID" value={String(config.etherscan_chain_id ?? "1")} onChange={(etherscan_chain_id) => setConfig({ etherscan_chain_id })} />
            <Switch checked={Boolean(config.blackrock_free_enabled ?? true)} onChange={(blackrock_free_enabled) => setConfig({ blackrock_free_enabled })} label="IBIT 免费监控" />
            <Switch checked={ibitNotificationEnabled(config, "blackrock_btc_address_operation_notification_enabled")} onChange={(blackrock_btc_address_operation_notification_enabled) => setConfig({ blackrock_btc_address_operation_notification_enabled })} label="BTC链上地址操作提醒" />
            <Switch checked={ibitNotificationEnabled(config, "blackrock_btc_outflow_notification_enabled")} onChange={(blackrock_btc_outflow_notification_enabled) => setConfig({ blackrock_btc_outflow_notification_enabled })} label="BTC地址簇大额转出提醒" />
            <Switch checked={ibitNotificationEnabled(config, "ibit_news_candidate_notification_enabled")} onChange={(ibit_news_candidate_notification_enabled) => setConfig({ ibit_news_candidate_notification_enabled })} label="新闻线索匹配提醒" />
            <Switch checked={ibitNotificationEnabled(config, "blackrock_etf_flow_notification_enabled")} onChange={(blackrock_etf_flow_notification_enabled) => setConfig({ blackrock_etf_flow_notification_enabled })} label="ETF资金流提醒" />
            <TextField label="iShares IBIT 页面" value={String(config.blackrock_ishares_url ?? "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf")} onChange={(blackrock_ishares_url) => setConfig({ blackrock_ishares_url })} />
            <TextField label="Farside ETF资金流" value={String(config.blackrock_farside_url ?? "https://farside.co.uk/btc/")} onChange={(blackrock_farside_url) => setConfig({ blackrock_farside_url })} />
            <TextField label="BTC Explorer API" value={String(config.blackrock_blockstream_api ?? "https://blockstream.info/api")} onChange={(blackrock_blockstream_api) => setConfig({ blackrock_blockstream_api })} />
            <NumberField label="IBIT ETF净流入提醒美元" value={config.blackrock_flow_alert_min_usd ?? 50000000} onChange={(blackrock_flow_alert_min_usd) => setConfig({ blackrock_flow_alert_min_usd })} />
            <NumberField label="BTC地址簇转出阈值" value={config.blackrock_btc_transfer_min_btc ?? 1000} onChange={(blackrock_btc_transfer_min_btc) => setConfig({ blackrock_btc_transfer_min_btc })} />
            <NumberField label="BTC地址簇回看小时" value={config.blackrock_btc_lookback_hours ?? 24} onChange={(blackrock_btc_lookback_hours) => setConfig({ blackrock_btc_lookback_hours: Math.max(1, blackrock_btc_lookback_hours) })} />
            <Switch checked={Boolean(config.ibit_news_enabled)} onChange={(ibit_news_enabled) => setConfig({ ibit_news_enabled })} label="IBIT 新闻线索" />
            <TextField label="IBIT新闻RSS" value={arr(config.ibit_news_rss_urls).join(",")} onChange={(value) => setConfig({ ibit_news_rss_urls: split(value) })} />
            <TextField label="IBIT新闻关键词" value={arr(config.ibit_news_keywords).join(",")} onChange={(value) => setConfig({ ibit_news_keywords: split(value) })} />
            <NumberField label="新闻回看小时" value={config.ibit_news_lookback_hours ?? 72} onChange={(ibit_news_lookback_hours) => setConfig({ ibit_news_lookback_hours: Math.max(1, ibit_news_lookback_hours) })} />
            <NumberField label="新闻最大条数" value={config.ibit_news_max_items ?? 60} onChange={(ibit_news_max_items) => setConfig({ ibit_news_max_items: Math.max(1, ibit_news_max_items) })} />
            <NumberField label="新闻提醒置信度" value={config.ibit_news_candidate_notify_min_confidence ?? 0.6} step="0.05" onChange={(ibit_news_candidate_notify_min_confidence) => setConfig({ ibit_news_candidate_notify_min_confidence })} />
            <Switch checked={Boolean(config.btc_candidate_monitor_enabled ?? true)} onChange={(btc_candidate_monitor_enabled) => setConfig({ btc_candidate_monitor_enabled })} label="BTC 大额底表" />
            <Switch checked={Boolean(config.eth_candidate_monitor_enabled ?? true)} onChange={(eth_candidate_monitor_enabled) => setConfig({ eth_candidate_monitor_enabled })} label="ETH 大额底表" />
            <NumberField label="底表最小 BTC" value={config.btc_candidate_min_btc ?? 500} onChange={(btc_candidate_min_btc) => setConfig({ btc_candidate_min_btc: Math.max(1, btc_candidate_min_btc) })} />
            <NumberField label="底表最小 ETH" value={config.eth_candidate_min_eth ?? 5000} onChange={(eth_candidate_min_eth) => setConfig({ eth_candidate_min_eth: Math.max(1, eth_candidate_min_eth) })} />
            <NumberField label="首次补扫区块" value={config.btc_candidate_backfill_blocks ?? 3} onChange={(btc_candidate_backfill_blocks) => setConfig({ btc_candidate_backfill_blocks: Math.max(1, btc_candidate_backfill_blocks) })} />
            <NumberField label="每轮扫描区块" value={config.btc_candidate_scan_blocks_per_run ?? 1} onChange={(btc_candidate_scan_blocks_per_run) => setConfig({ btc_candidate_scan_blocks_per_run: Math.max(1, btc_candidate_scan_blocks_per_run) })} />
            <NumberField label="ETH新闻回扫块/线索" value={config.eth_candidate_history_blocks_per_news ?? 1440} onChange={(eth_candidate_history_blocks_per_news) => setConfig({ eth_candidate_history_blocks_per_news: Math.max(1, eth_candidate_history_blocks_per_news) })} />
            <NumberField label="底表保留天数" value={config.btc_candidate_retention_days ?? 90} onChange={(btc_candidate_retention_days) => setConfig({ btc_candidate_retention_days: Math.max(1, btc_candidate_retention_days) })} />
            <NumberField label="底表匹配窗口小时" value={config.btc_candidate_match_window_hours ?? 48} onChange={(btc_candidate_match_window_hours) => setConfig({ btc_candidate_match_window_hours: Math.max(1, btc_candidate_match_window_hours) })} />
            <NumberField label="金额容差 %" value={config.btc_candidate_amount_tolerance_pct ?? 8} onChange={(btc_candidate_amount_tolerance_pct) => setConfig({ btc_candidate_amount_tolerance_pct: Math.max(1, btc_candidate_amount_tolerance_pct) })} />
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

function WhaleStrategyEditor({
  strategy,
  notifiers,
  whaleTargets,
  setNotice,
  onChange,
  onSave,
  disabled
}: {
  strategy: MutableStrategy;
  notifiers: NotifierTarget[];
  whaleTargets: WhaleTarget[];
  setNotice: (notice: string) => void;
  onChange: (strategy: MutableStrategy) => void;
  onSave: () => void;
  disabled: boolean;
}) {
  const config = strategy.config;
  const setConfig = (patch: Record<string, unknown>) => onChange({ ...strategy, config: { ...config, ...patch } });
  return (
    <div className="strategy-editor strategy-editor--whale">
      <div className="strategy-editor__head">
        <strong>{strategyLabels[strategy.id] ?? strategy.name}</strong>
        <Switch checked={strategy.enabled} onChange={(enabled) => onChange({ ...strategy, enabled })} />
      </div>
      <div className="whale-strategy-layout">
        <section className="whale-strategy-card">
          <div className="whale-strategy-card__head">
            <strong>巨鲸通用</strong>
            <span>0x / Hyperliquid / DeBank</span>
          </div>
          <div className="form-grid">
            <Switch checked={Boolean(config.enabled)} onChange={(enabled) => setConfig({ enabled })} label="启用巨鲸 worker" />
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
          </div>
          <div className="field-help-list">
            <span><b>常改</b>：启用状态、Hyperliquid/DeBank 开关、阈值。</span>
            <span><b>少改</b>：API 地址、轮询秒。除非接口变更或限频，不建议动。</span>
            <span><b>提醒</b>：巨鲸成交只要关注对象有新操作就提醒，阈值只影响前台“大额”标记。</span>
          </div>
        </section>
        <section className="whale-strategy-card whale-strategy-card--ibit">
          <IbitStrategySettings strategy={strategy} config={config} setConfig={setConfig} setNotice={setNotice} notifiers={notifiers} whaleTargets={whaleTargets} onSaveStrategy={onSave} />
        </section>
      </div>
      <div className="strategy-editor__footer">
        <select value={strategy.notifier_id ?? ""} onChange={(event) => onChange({ ...strategy, notifier_id: event.target.value || null })}>
          <option value="">不绑定机器人</option>
          {notifiers.map((notifier) => <option value={notifier.id} key={notifier.id}>{notifier.name}</option>)}
        </select>
        <button onClick={onSave} disabled={disabled}><Save size={16} /> 保存</button>
      </div>
    </div>
  );
}

function IbitStrategySettings({
  strategy,
  config,
  setConfig,
  setNotice,
  notifiers,
  whaleTargets,
  onSaveStrategy
}: {
  strategy: MutableStrategy;
  config: Record<string, any>;
  setConfig: (patch: Record<string, unknown>) => void;
  setNotice: (notice: string) => void;
  notifiers: NotifierTarget[];
  whaleTargets: WhaleTarget[];
  onSaveStrategy: () => void;
}) {
  return (
    <div className="ibit-settings">
      <div className="whale-strategy-card__head">
        <strong>IBIT 免费监控</strong>
        <span>BTC/ETH 链上底表 / ETF资金流 / 新闻匹配</span>
      </div>
      <IbitNotifierBindingStatus strategy={strategy} notifiers={notifiers} whaleTargets={whaleTargets} />
      <div className="form-grid">
        <Switch checked={Boolean(config.blackrock_free_enabled ?? true)} onChange={(blackrock_free_enabled) => setConfig({ blackrock_free_enabled })} label="启用 IBIT" />
        <Switch checked={ibitNotificationEnabled(config, "blackrock_btc_address_operation_notification_enabled")} onChange={(blackrock_btc_address_operation_notification_enabled) => setConfig({ blackrock_btc_address_operation_notification_enabled })} label="BTC链上地址操作提醒" />
        <Switch checked={ibitNotificationEnabled(config, "blackrock_btc_outflow_notification_enabled")} onChange={(blackrock_btc_outflow_notification_enabled) => setConfig({ blackrock_btc_outflow_notification_enabled })} label="BTC地址簇大额转出提醒" />
        <Switch checked={ibitNotificationEnabled(config, "ibit_news_candidate_notification_enabled")} onChange={(ibit_news_candidate_notification_enabled) => setConfig({ ibit_news_candidate_notification_enabled })} label="新闻线索匹配提醒" />
        <Switch checked={ibitNotificationEnabled(config, "blackrock_etf_flow_notification_enabled")} onChange={(blackrock_etf_flow_notification_enabled) => setConfig({ blackrock_etf_flow_notification_enabled })} label="ETF资金流提醒" />
        <NumberField label="ETF净流入提醒美元" value={config.blackrock_flow_alert_min_usd ?? 50000000} onChange={(blackrock_flow_alert_min_usd) => setConfig({ blackrock_flow_alert_min_usd })} />
        <NumberField label="BTC 地址回看小时" value={config.blackrock_btc_lookback_hours ?? 24} onChange={(blackrock_btc_lookback_hours) => setConfig({ blackrock_btc_lookback_hours: Math.max(1, blackrock_btc_lookback_hours) })} />
        <Switch checked={Boolean(config.ibit_news_enabled)} onChange={(ibit_news_enabled) => setConfig({ ibit_news_enabled })} label="IBIT 新闻线索" />
        <TextField label="新闻 RSS" value={arr(config.ibit_news_rss_urls).join(",")} onChange={(value) => setConfig({ ibit_news_rss_urls: split(value) })} />
        <TextField label="新闻关键词" value={arr(config.ibit_news_keywords).join(",")} onChange={(value) => setConfig({ ibit_news_keywords: split(value) })} />
        <NumberField label="新闻回看小时" value={config.ibit_news_lookback_hours ?? 72} onChange={(ibit_news_lookback_hours) => setConfig({ ibit_news_lookback_hours: Math.max(1, ibit_news_lookback_hours) })} />
        <NumberField label="新闻最大条数" value={config.ibit_news_max_items ?? 60} onChange={(ibit_news_max_items) => setConfig({ ibit_news_max_items: Math.max(1, ibit_news_max_items) })} />
        <NumberField label="新闻提醒置信度" value={config.ibit_news_candidate_notify_min_confidence ?? 0.6} step="0.05" onChange={(ibit_news_candidate_notify_min_confidence) => setConfig({ ibit_news_candidate_notify_min_confidence })} />
        <Switch checked={Boolean(config.btc_candidate_monitor_enabled ?? true)} onChange={(btc_candidate_monitor_enabled) => setConfig({ btc_candidate_monitor_enabled })} label="BTC 大额底表" />
        <Switch checked={Boolean(config.eth_candidate_monitor_enabled ?? true)} onChange={(eth_candidate_monitor_enabled) => setConfig({ eth_candidate_monitor_enabled })} label="ETH 大额底表" />
        <Switch checked={Boolean(config.etherscan_enabled)} onChange={(etherscan_enabled) => setConfig({ etherscan_enabled })} label="Etherscan 数据源" />
        <NumberField label="底表最小 BTC" value={config.btc_candidate_min_btc ?? 500} onChange={(btc_candidate_min_btc) => setConfig({ btc_candidate_min_btc: Math.max(1, btc_candidate_min_btc) })} />
        <NumberField label="底表最小 ETH" value={config.eth_candidate_min_eth ?? 5000} onChange={(eth_candidate_min_eth) => setConfig({ eth_candidate_min_eth: Math.max(1, eth_candidate_min_eth) })} />
        <NumberField label="底表匹配窗口小时" value={config.btc_candidate_match_window_hours ?? 48} onChange={(btc_candidate_match_window_hours) => setConfig({ btc_candidate_match_window_hours: Math.max(1, btc_candidate_match_window_hours) })} />
        <NumberField label="金额容差 %" value={config.btc_candidate_amount_tolerance_pct ?? 8} onChange={(btc_candidate_amount_tolerance_pct) => setConfig({ btc_candidate_amount_tolerance_pct: Math.max(1, btc_candidate_amount_tolerance_pct) })} />
        <TextField label="Etherscan API Key" value={String(config.etherscan_api_key ?? "")} onChange={(etherscan_api_key) => setConfig({ etherscan_api_key })} />
        <TextField label="Etherscan Chain ID" value={String(config.etherscan_chain_id ?? "1")} onChange={(etherscan_chain_id) => setConfig({ etherscan_chain_id })} />
      </div>
      <details className="advanced-settings">
        <summary>高级源配置</summary>
        <div className="form-grid">
          <TextField label="iShares 页面" value={String(config.blackrock_ishares_url ?? "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf")} onChange={(blackrock_ishares_url) => setConfig({ blackrock_ishares_url })} />
          <TextField label="Farside ETF资金流" value={String(config.blackrock_farside_url ?? "https://farside.co.uk/btc/")} onChange={(blackrock_farside_url) => setConfig({ blackrock_farside_url })} />
          <TextField label="BTC Explorer API" value={String(config.blackrock_blockstream_api ?? "https://blockstream.info/api")} onChange={(blackrock_blockstream_api) => setConfig({ blackrock_blockstream_api })} />
          <NumberField label="地址簇转出阈值" value={config.blackrock_btc_transfer_min_btc ?? 1000} onChange={(blackrock_btc_transfer_min_btc) => setConfig({ blackrock_btc_transfer_min_btc })} />
          <NumberField label="首次补扫区块" value={config.btc_candidate_backfill_blocks ?? 3} onChange={(btc_candidate_backfill_blocks) => setConfig({ btc_candidate_backfill_blocks: Math.max(1, btc_candidate_backfill_blocks) })} />
          <NumberField label="每轮扫描区块" value={config.btc_candidate_scan_blocks_per_run ?? 1} onChange={(btc_candidate_scan_blocks_per_run) => setConfig({ btc_candidate_scan_blocks_per_run: Math.max(1, btc_candidate_scan_blocks_per_run) })} />
          <NumberField label="ETH每轮扫描区块" value={config.eth_candidate_scan_blocks_per_run ?? 1} onChange={(eth_candidate_scan_blocks_per_run) => setConfig({ eth_candidate_scan_blocks_per_run: Math.max(1, eth_candidate_scan_blocks_per_run) })} />
          <NumberField label="Etherscan 请求间隔秒" value={config.etherscan_min_request_interval_seconds ?? 0.25} step="0.05" onChange={(etherscan_min_request_interval_seconds) => setConfig({ etherscan_min_request_interval_seconds: Math.max(0.2, etherscan_min_request_interval_seconds) })} />
          <NumberField label="ETH新闻回扫块/线索" value={config.eth_candidate_history_blocks_per_news ?? 1440} onChange={(eth_candidate_history_blocks_per_news) => setConfig({ eth_candidate_history_blocks_per_news: Math.max(1, eth_candidate_history_blocks_per_news) })} />
          <NumberField label="底表保留天数" value={config.btc_candidate_retention_days ?? 90} onChange={(btc_candidate_retention_days) => setConfig({ btc_candidate_retention_days: Math.max(1, btc_candidate_retention_days) })} />
        </div>
      </details>
      <IbitTargetSettings setNotice={setNotice} onSaveStrategy={onSaveStrategy} />
      <div className="field-help-list">
        <span><b>需要改</b>：已订阅 BTC 地址、四个 IBIT 提醒开关、新闻 RSS/关键词、底表最小 BTC/ETH、Etherscan API Key、新闻提醒置信度。</span>
        <span><b>提醒开关</b>：BTC 链上地址操作、BTC 地址簇大额转出、新闻线索匹配、ETF 资金流现在分别控制。</span>
        <span><b>通常不用动</b>：iShares、Farside、BTC Explorer、补扫区块、保留天数。</span>
        <span><b>候选地址</b>：现在只是辅助池；真正持续监控和飞书筛选看“已订阅 BTC 地址”。</span>
      </div>
    </div>
  );
}

function IbitNotifierBindingStatus({
  strategy,
  notifiers,
  whaleTargets
}: {
  strategy: MutableStrategy;
  notifiers: NotifierTarget[];
  whaleTargets: WhaleTarget[];
}) {
  const notifierId = String(strategy.notifier_id ?? "").trim();
  const notifier = notifierId ? notifiers.find((item) => item.id === notifierId) : null;
  const ibitTarget = whaleTargets.find(isBlackRockTarget) ?? null;
  const ibitTargetId = ibitTarget?.id ?? "";
  const targetFilter = arr(notifier?.config?.whale_target_ids);
  const btcFilter = arr(notifier?.config?.whale_btc_addresses);
  const targetById = new Map(whaleTargets.map((target) => [target.id, target]));
  const labels = ibitTarget?.config?.btc_address_labels && typeof ibitTarget.config.btc_address_labels === "object"
    ? ibitTarget.config.btc_address_labels as Record<string, any>
    : {};
  const subscribedAddresses = configuredBtcAddresses(ibitTarget);
  const subscribedSet = new Set(subscribedAddresses.map(normalizeAddress));
  const selectedTargetLabels = targetFilter.map((id) => targetById.get(id)?.label ?? id);
  const selectedBtcLabels = btcFilter.map((address) => btcAddressLabel(address, labels, ibitTarget?.label ?? "BTC 地址"));
  const staleBtcFilters = btcFilter.filter((address) => !subscribedSet.has(normalizeAddress(address)));
  const extraTargets = targetFilter.filter((id) => id !== ibitTargetId);
  const reminderSwitches = [
    ibitNotificationEnabled(strategy.config, "blackrock_btc_address_operation_notification_enabled"),
    ibitNotificationEnabled(strategy.config, "blackrock_btc_outflow_notification_enabled"),
    ibitNotificationEnabled(strategy.config, "ibit_news_candidate_notification_enabled"),
    ibitNotificationEnabled(strategy.config, "blackrock_etf_flow_notification_enabled")
  ];
  const issues: string[] = [];

  if (!notifierId) {
    issues.push("巨鲸/IBIT 策略还没有绑定机器人，IBIT 飞书提醒不会发送。");
  } else if (!notifier) {
    issues.push(`策略绑定的机器人「${notifierId}」不存在，请在 Webhook 机器人里新增或重新选择。`);
  } else {
    if (!notifier.enabled) issues.push(`当前绑定机器人「${notifier.name || notifier.id}」未启用。`);
    if (notifier.type !== "feishu") issues.push("当前绑定的不是飞书机器人，请确认是否要把 IBIT 提醒发到这个通道。");
    if (!targetFilter.length) issues.push("机器人未指定账户范围：当前会接收全部巨鲸/IBIT 账户，若只收 IBIT 请勾选 IBIT 账户。");
    if (ibitTargetId && targetFilter.length && !targetFilter.includes(ibitTargetId)) {
      issues.push("机器人账户范围未包含 IBIT 账户，IBIT 事件会被过滤掉。");
    }
    if (extraTargets.length) {
      issues.push(`账户范围还包含 ${extraTargets.map((id) => targetById.get(id)?.label ?? id).join("、")}，这个机器人会同时收到这些账户提醒。`);
    }
    if (!btcFilter.length) issues.push("机器人未指定 BTC 地址范围：当前会接收所选账户下全部 BTC 链事件。");
    if (staleBtcFilters.length) {
      issues.push(`BTC 地址范围里有 ${staleBtcFilters.length} 条不在 IBIT 已订阅地址中，请清理无效筛选。`);
    }
    if (btcFilter.length) {
      issues.push("已限制到具体 BTC 地址：ETF资金流、iShares 官方持仓这类不带链上地址的 IBIT 事件可能不会发送到该机器人。");
    }
  }
  if (!reminderSwitches.some(Boolean)) {
    issues.push("4 个 IBIT 提醒开关全部关闭，IBIT 事件会记录在系统里但不会发飞书。");
  }
  if (!Boolean(strategy.config.ibit_news_enabled) && ibitNotificationEnabled(strategy.config, "ibit_news_candidate_notification_enabled")) {
    issues.push("新闻线索采集未启用，新闻线索匹配提醒不会产生。");
  }

  if (!ibitTarget) {
    issues.push("还没有 IBIT 账户记录，请先在下面的 IBIT 地址管理里保存。");
  } else if (!subscribedAddresses.length) {
    issues.push("IBIT 账户还没有已订阅 BTC 地址，链上地址操作不会产生提醒。");
  }

  const hasIssue = issues.length > 0;
  const boundLabel = notifier ? `${notifier.name || notifier.id}（${notifier.id}）` : notifierId ? `未找到：${notifierId}` : "未绑定";
  const accountScope = !notifier
    ? "未配置"
    : targetFilter.length ? selectedTargetLabels.join("、") : "全部巨鲸/IBIT 账户";
  const btcScope = !notifier
    ? "未配置"
    : btcFilter.length ? selectedBtcLabels.join("、") : "全部 BTC 链事件";
  const ibitScope = ibitTarget
    ? `${ibitTarget.label} · ${subscribedAddresses.length} 条已订阅 BTC 链`
    : "未创建 IBIT 账户";

  return (
    <div className={`ibit-notifier-status ${hasIssue ? "ibit-notifier-status--warning" : "ibit-notifier-status--ok"}`}>
      <div className="ibit-notifier-status__head">
        <strong>当前绑定机器人 + 接收范围</strong>
        <span>{hasIssue ? "需要确认" : "配置正常"}</span>
      </div>
      <div className="ibit-notifier-status__grid">
        <span><b>绑定机器人</b><em>{boundLabel}</em></span>
        <span><b>账户范围</b><em>{accountScope}</em></span>
        <span><b>BTC 地址范围</b><em>{btcScope}</em></span>
        <span><b>IBIT 订阅</b><em>{ibitScope}</em></span>
      </div>
      {hasIssue ? (
        <div className="ibit-notifier-status__issues">
          {issues.map((issue) => <span key={issue}>{issue}</span>)}
        </div>
      ) : (
        <p>这个机器人会接收 IBIT 账户范围内的链上地址、新闻线索和 ETF 资金流事件。</p>
      )}
    </div>
  );
}

function IbitTargetSettings({ setNotice, onSaveStrategy }: { setNotice: (notice: string) => void; onSaveStrategy: () => void }) {
  const queryClient = useQueryClient();
  const whalesQuery = useQuery({ queryKey: ["whales"], queryFn: api.whales });
  const target = (whalesQuery.data ?? []).find(isBlackRockTarget);
  const [draft, setDraft] = useState<WhaleTargetUpsert | null>(null);
  const [newAddress, setNewAddress] = useState("");
  const [newSuspectedAddress, setNewSuspectedAddress] = useState("");
  useEffect(() => {
    if (target) setDraft(whaleToDraft(target));
  }, [target]);
  const saveTarget = useMutation({
    mutationFn: api.saveWhaleTarget,
    onSuccess: () => {
      setNotice("IBIT 地址订阅已保存");
      void queryClient.invalidateQueries({ queryKey: ["whales"] });
      void queryClient.invalidateQueries({ queryKey: ["snapshot"] });
      if (target?.id) void queryClient.invalidateQueries({ queryKey: ["whale", target.id] });
    },
    onError: (error) => setNotice(`IBIT 地址保存失败：${readErrorMessage(error)}`)
  });
  const config = draft?.config ?? {};
  const labels = config.btc_address_labels && typeof config.btc_address_labels === "object" ? config.btc_address_labels as Record<string, any> : {};
  const addresses = arr(config.btc_addresses);
  const suspected = arr(config.suspected_btc_addresses);
  const updateConfig = (patch: Record<string, unknown>) => {
    setDraft((current) => current ? { ...current, config: { ...current.config, ...patch } } : current);
  };
  const addAddress = () => {
    const address = newAddress.trim();
    if (!address) return;
    updateConfig({ btc_addresses: uniqueStrings([...addresses, address]) });
    setNewAddress("");
  };
  const removeAddress = (address: string) => {
    const nextLabels = { ...labels };
    delete nextLabels[address];
    delete nextLabels[address.toLowerCase()];
    updateConfig({
      btc_addresses: addresses.filter((item) => item.toLowerCase() !== address.toLowerCase()),
      btc_address_labels: nextLabels
    });
  };
  const updateLabel = (address: string, label: string) => {
    updateConfig({ btc_address_labels: { ...labels, [address]: label } });
  };
  const addSuspectedAddress = () => {
    const address = newSuspectedAddress.trim();
    if (!address) return;
    updateConfig({ suspected_btc_addresses: uniqueStrings([...suspected, address]) });
    setNewSuspectedAddress("");
  };
  const subscribeSuspected = (address: string) => {
    updateConfig({
      btc_addresses: uniqueStrings([...addresses, address]),
      suspected_btc_addresses: suspected.filter((item) => item.toLowerCase() !== address.toLowerCase())
    });
  };
  const removeSuspectedAddress = (address: string) => {
    updateConfig({ suspected_btc_addresses: suspected.filter((item) => item.toLowerCase() !== address.toLowerCase()) });
  };
  const saveIbitSettings = () => {
    if (!draft) return;
    onSaveStrategy();
    saveTarget.mutate(draft);
  };
  if (!draft) return <span className="empty">正在加载 IBIT 目标配置…</span>;
  return (
    <div className="ibit-target-settings">
      <div className="ibit-target-settings__head">
        <strong>IBIT 地址管理</strong>
        <Switch checked={draft.enabled} onChange={(enabled) => setDraft({ ...draft, enabled })} label="启用目标" />
      </div>
      <div className="form-grid">
        <TextField label="显示名称" value={draft.label} onChange={(label) => setDraft({ ...draft, label })} />
        <TextField label="来源链接" value={String(draft.config.source_url ?? "")} onChange={(source_url) => updateConfig({ source_url })} />
      </div>
      <div className="address-manager">
        <div className="address-manager__head">
          <strong>已订阅 BTC 地址</strong>
          <span>{addresses.length} 条</span>
        </div>
        {addresses.map((address) => (
          <div className="address-manager__row" key={address}>
            <code title={address}>{shortText(address, 14)}</code>
            <input value={String(labels[address] ?? labels[address.toLowerCase()] ?? "")} onChange={(event) => updateLabel(address, event.target.value)} placeholder="地址命名，例如 贝莱德 / IBIT" />
            <button className="danger-button" onClick={() => removeAddress(address)}><Trash2 size={14} /> 取消订阅</button>
          </div>
        ))}
        {!addresses.length && <span className="empty">暂无订阅地址。可从 IBIT 新闻线索或大额底表点击订阅，也可以在这里手动添加。</span>}
        <div className="address-manager__add">
          <input value={newAddress} onChange={(event) => setNewAddress(event.target.value)} placeholder="粘贴 BTC 地址" />
          <button onClick={addAddress} disabled={!newAddress.trim()}><Plus size={14} /> 添加订阅</button>
        </div>
      </div>
      <details className="advanced-settings">
        <summary>疑似地址池（辅助）</summary>
        <div className="address-manager">
          {suspected.map((address) => (
            <div className="address-manager__row" key={address}>
              <code title={address}>{shortText(address, 14)}</code>
              <span className="muted">不会按订阅地址推送；确认后再订阅。</span>
              <button onClick={() => subscribeSuspected(address)}><Plus size={14} /> 转为订阅</button>
              <button className="danger-button" onClick={() => removeSuspectedAddress(address)}><Trash2 size={14} /> 删除</button>
            </div>
          ))}
          {!suspected.length && <span className="empty">暂无疑似地址；这不是主流程，可以不维护。</span>}
          <div className="address-manager__add">
            <input value={newSuspectedAddress} onChange={(event) => setNewSuspectedAddress(event.target.value)} placeholder="可选：手动加入疑似 BTC 地址" />
            <button onClick={addSuspectedAddress} disabled={!newSuspectedAddress.trim()}><Plus size={14} /> 加入疑似</button>
          </div>
        </div>
      </details>
      <button className="primary-action" onClick={saveIbitSettings} disabled={saveTarget.isPending || !draft.label.trim() || !draft.address_or_subject.trim()}><Save size={16} /> 保存 IBIT 配置</button>
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

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  values.map((value) => value.trim()).filter(Boolean).forEach((value) => {
    const key = value.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    result.push(value);
  });
  return result;
}

function normalizeAddress(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

function isBtcAddress(value: unknown) {
  return /^(?:bc1[ac-hj-np-z02-9]{11,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$/i.test(String(value ?? "").trim());
}

function extractBtcAddressesFromText(value: unknown): string[] {
  const text = String(value ?? "");
  return uniqueStrings(text.match(/\b(?:bc1[ac-hj-np-z02-9]{11,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b/gi) ?? []);
}

function configuredBtcAddresses(target?: WhaleTarget | null): string[] {
  if (!target) return [];
  return uniqueStrings([
    ...extractBtcAddressesFromText(target.address_or_subject),
    ...arr(target.config?.btc_addresses)
  ]);
}

function subscribedBtcAddressSet(target?: WhaleTarget | null) {
  return new Set(configuredBtcAddresses(target).map(normalizeAddress));
}

function isSubscribedBtcAddress(target: WhaleTarget, address: string) {
  return subscribedBtcAddressSet(target).has(normalizeAddress(address));
}

function rowsForAddress(value: unknown, address: string): Array<Record<string, any>> {
  if (!value || typeof value !== "object") return [];
  const map = value as Record<string, unknown>;
  const direct = map[address] ?? map[address.toLowerCase()];
  return Array.isArray(direct) ? direct as Array<Record<string, any>> : [];
}

function isBlackRockTarget(target: WhaleTarget) {
  return String(target.config?.provider ?? "").trim().toLowerCase() === "blackrock_free_monitor";
}

function ibitAddressRows(target: WhaleTarget, detail?: WhaleDetail | null): BtcAddressView[] {
  const raw = detail?.snapshot?.raw ?? {};
  const cluster = raw.btc_cluster && typeof raw.btc_cluster === "object" ? raw.btc_cluster as Record<string, any> : {};
  const news = raw.news_signals && typeof raw.news_signals === "object" ? raw.news_signals as Record<string, any> : {};
  const activity = cluster.address_activity && typeof cluster.address_activity === "object" ? cluster.address_activity as Record<string, any> : {};
  const confirmedActivity = news.confirmed_address_activity && typeof news.confirmed_address_activity === "object" ? news.confirmed_address_activity as Record<string, any> : {};
  const suspectedActivity = news.suspected_address_activity && typeof news.suspected_address_activity === "object" ? news.suspected_address_activity as Record<string, any> : {};
  const labels = target.config?.btc_address_labels && typeof target.config.btc_address_labels === "object" ? target.config.btc_address_labels as Record<string, any> : {};
  const rows = new Map<string, BtcAddressView>();
  const upsert = (address: string, patch: Partial<BtcAddressView>) => {
    const key = address.trim();
    if (!key) return;
    const current = rows.get(key);
    const operations = patch.operations ?? current?.operations ?? [];
    const signals = patch.signals ?? current?.signals ?? [];
    const reasons = patch.reasons ?? current?.reasons ?? [];
    rows.set(key, {
      targetId: target.id,
      targetLabel: target.label,
      address: key,
      label: String(patch.label ?? current?.label ?? btcAddressLabel(key, labels, target.label)),
      role: patch.role ?? current?.role ?? "suspected",
      confidence: Math.max(Number(current?.confidence ?? 0), Number(patch.confidence ?? 0)),
      operations: mergeOperations(current?.operations ?? [], operations),
      signals: mergeSignals(current?.signals ?? [], signals),
      reasons: Array.from(new Set([...(current?.reasons ?? []), ...reasons.map(String)]))
    });
  };
  const confirmedAddresses = configuredBtcAddresses(target);
  const confirmedAddressSet = new Set(confirmedAddresses.map(normalizeAddress));
  confirmedAddresses.forEach((address) => {
    upsert(address, {
      role: "confirmed",
      operations: mergeOperations(rowsForAddress(activity, address), rowsForAddress(confirmedActivity, address)),
      confidence: 1
    });
  });
  arr(target.config?.suspected_btc_addresses)
    .filter((address) => !confirmedAddressSet.has(normalizeAddress(address)))
    .forEach((address) => {
      upsert(address, {
        role: "suspected",
        operations: rowsForAddress(suspectedActivity, address),
        confidence: 0.5,
        reasons: ["后台手动加入疑似地址池"]
      });
    });
  const suspected = Array.isArray(news.suspected_addresses) ? news.suspected_addresses as Array<Record<string, any>> : [];
  suspected.forEach((item) => {
    const address = String(item.address ?? "");
    if (!address) return;
    upsert(address, {
      role: confirmedAddressSet.has(normalizeAddress(address)) ? "confirmed" : "suspected",
      label: btcAddressLabel(address, labels, target.label),
      confidence: Number(item.confidence ?? 0),
      operations: Array.isArray(item.latest_operations) ? item.latest_operations as Array<Record<string, any>> : [],
      signals: Array.isArray(item.signals) ? item.signals as Array<Record<string, any>> : [],
      reasons: Array.isArray(item.reasons) ? item.reasons.map(String) : []
    });
  });
  return Array.from(rows.values()).sort((a, b) => {
    if (a.role !== b.role) return a.role === "confirmed" ? -1 : 1;
    return b.confidence - a.confidence || b.operations.length - a.operations.length;
  });
}

function btcAddressLabel(address: string, labels: Record<string, any>, fallback: string) {
  const exact = labels[address] ?? labels[address.toLowerCase()];
  if (exact) return String(exact);
  if (/blackrock|ibit|贝莱德/i.test(fallback)) return "贝莱德 / IBIT";
  return fallback || shortText(address, 8);
}

function mergeOperations(a: Array<Record<string, any>>, b: Array<Record<string, any>>) {
  const rows = new Map<string, Record<string, any>>();
  [...a, ...b].forEach((item) => {
    const key = `${String(item.txid ?? "")}:${String(item.direction ?? item.behavior ?? "")}`;
    if (key.trim() !== ":") rows.set(key, item);
  });
  return Array.from(rows.values()).sort((left, right) => Number(right.timestamp_ms ?? 0) - Number(left.timestamp_ms ?? 0));
}

function mergeSignals(a: Array<Record<string, any>>, b: Array<Record<string, any>>) {
  const rows = new Map<string, Record<string, any>>();
  [...a, ...b].forEach((item) => rows.set(newsSignalKey(item), item));
  return Array.from(rows.values()).sort((left, right) => String(right.published_at ?? "").localeCompare(String(left.published_at ?? "")));
}

function btcCounterpartyText(operation: Record<string, any>) {
  const direction = String(operation.direction ?? "");
  const source = direction === "out" ? operation.output_counterparties : operation.input_counterparties;
  if (!Array.isArray(source)) return "";
  return source.slice(0, 3).map((item) => {
    if (item && typeof item === "object") {
      const address = String((item as Record<string, any>).address ?? "");
      const value = Number((item as Record<string, any>).value_btc ?? 0);
      return value > 0 ? `${shortText(address, 8)} ${formatNumber(value, 4)} BTC` : shortText(address, 8);
    }
    return shortText(item, 8);
  }).filter(Boolean).join(" / ");
}

function addressActivityRows(value: unknown): Record<string, any>[] {
  if (!value || typeof value !== "object") return [];
  const rows: Record<string, any>[] = Object.entries(value as Record<string, unknown>)
    .flatMap(([address, rows]) => Array.isArray(rows) ? rows.map((row) => ({ ...(row as Record<string, any>), address: String((row as Record<string, any>).address ?? address) })) : []);
  return rows
    .sort((a, b) => Number(b.timestamp_ms ?? 0) - Number(a.timestamp_ms ?? 0))
    .slice(0, 80);
}

function addressActivityCount(value: unknown) {
  if (!value || typeof value !== "object") return 0;
  return Object.values(value as Record<string, unknown>).reduce<number>((total, rows) => total + (Array.isArray(rows) ? rows.length : 0), 0);
}

function newsSignalKey(signal: Record<string, any>) {
  return String(signal.id ?? signal.url ?? signal.title ?? "");
}

function suspectedMatchesForSignal(items: Record<string, any>[], signal: Record<string, any>) {
  const id = String(signal.id ?? "");
  const url = String(signal.url ?? "");
  const title = String(signal.title ?? "");
  return items
    .map((item) => {
      const signals = Array.isArray(item.signals) ? item.signals as Record<string, any>[] : [];
      const matched = signals.find((candidate) => (
        (id && String(candidate.id ?? "") === id)
        || (url && String(candidate.url ?? "") === url)
        || (title && String(candidate.title ?? "") === title)
      ));
      if (!matched) return null;
      return {
        item,
        signal: matched,
        confidence: Number(matched.confidence ?? item.confidence ?? 0)
      };
    })
    .filter((value): value is { item: Record<string, any>; signal: Record<string, any>; confidence: number } => value !== null)
    .sort((a, b) => b.confidence - a.confidence || Number(b.item.signal_count ?? 0) - Number(a.item.signal_count ?? 0));
}

function newsMatchedAddressCount(signals: Record<string, any>[], items: Record<string, any>[]) {
  const addresses = new Set<string>();
  for (const signal of signals) {
    for (const match of suspectedMatchesForSignal(items, signal)) {
      addresses.add(String(match.item.address));
    }
  }
  return addresses.size;
}

function shortText(value: unknown, edge = 8) {
  const text = String(value ?? "");
  if (text.length <= edge * 2 + 3) return text;
  return `${text.slice(0, edge)}…${text.slice(-edge)}`;
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

function ibitNotificationEnabled(config: Record<string, any>, key: string) {
  return Boolean(config[key] ?? config.blackrock_free_notification_enabled ?? true);
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
    config: { whale_target_ids: [], whale_btc_addresses: [] },
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
  if (String(target.config.provider ?? "") === "blackrock_free_monitor") return false;
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
