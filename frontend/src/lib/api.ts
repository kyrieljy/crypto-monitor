import type {
  AlertEvent,
  DashboardLayout,
  DashboardModule,
  Kline,
  NewsEvent,
  NewsTranslateResult,
  NotifierTarget,
  Snapshot,
  SourceHealth,
  StrategyConfig,
  WhaleDetail,
  WhaleAddressResolveResponse,
  WhaleTarget,
  WhaleTargetUpsert,
  SymbolItem
} from "../types/api";

const API_BASE = "";

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("adminToken");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authHeaders()
  };
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  login: (password: string) => request<{ token: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  snapshot: () => request<Snapshot>("/api/snapshot"),
  symbols: () => request<SymbolItem[]>("/api/settings/symbols"),
  saveSymbols: (items: SymbolItem[]) => request<SymbolItem[]>("/api/settings/symbols", { method: "PUT", body: JSON.stringify(items) }),
  strategy: (id: string) => request<StrategyConfig>(`/api/strategies/${id}`),
  saveStrategy: (strategy: StrategyConfig) =>
    request<StrategyConfig>(`/api/strategies/${strategy.id}`, {
      method: "PUT",
      body: JSON.stringify({ enabled: strategy.enabled, config: strategy.config, notifier_id: strategy.notifier_id })
    }),
  notifiers: () => request<NotifierTarget[]>("/api/notifiers"),
  saveNotifiers: (items: NotifierTarget[]) => request<NotifierTarget[]>("/api/notifiers", { method: "PUT", body: JSON.stringify(items) }),
  testNotifier: (id: string) => request<{ ok: boolean; dry_run: boolean; message: string }>(`/api/notifiers/${id}/test`, { method: "POST" }),
  modules: () => request<DashboardModule[]>("/api/dashboard/modules"),
  saveModules: (items: DashboardModule[]) => request<DashboardModule[]>("/api/dashboard/modules", { method: "PUT", body: JSON.stringify(items) }),
  layout: () => request<DashboardLayout>("/api/dashboard/layout"),
  saveLayout: (layout: DashboardLayout) => request<DashboardLayout>("/api/dashboard/layout", { method: "PUT", body: JSON.stringify(layout) }),
  alerts: () => request<AlertEvent[]>("/api/events/alerts"),
  news: () => request<NewsEvent[]>("/api/events/news"),
  translateNews: (ids: number[]) => request<NewsTranslateResult>("/api/events/news/translate", { method: "POST", body: JSON.stringify({ ids }) }),
  health: () => request<SourceHealth[]>("/api/health/sources"),
  whales: () => request<WhaleTarget[]>("/api/whales"),
  resolveWhaleAddress: (query: string) => request<WhaleAddressResolveResponse>("/api/whales/resolve", { method: "POST", body: JSON.stringify({ query }) }),
  saveWhaleTarget: (target: WhaleTargetUpsert) => request<WhaleTarget>("/api/whales", { method: "PUT", body: JSON.stringify(target) }),
  deleteWhaleTarget: (id: string) => request<{ ok: boolean }>(`/api/whales/${id}`, { method: "DELETE" }),
  whaleDetail: (id: string) => request<WhaleDetail>(`/api/whales/${id}`),
  klines: (symbol: string, interval = "15m", limit = 90) => request<Kline[]>(`/api/market/klines/${symbol}/${interval}?limit=${limit}`)
};
