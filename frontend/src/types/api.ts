export type ThemeMode = "dark" | "light";

export interface SymbolItem {
  symbol: string;
  display_name: string;
  enabled: boolean;
  sort_order: number;
}

export interface StrategyConfig {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  config: Record<string, unknown>;
  notifier_id: string | null;
  updated_at: string;
}

export interface NotifierTarget {
  id: string;
  name: string;
  type: "feishu" | "telegram";
  enabled: boolean;
  secrets: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface DashboardModule {
  id: string;
  title: string;
  enabled: boolean;
  visible: boolean;
  config: Record<string, unknown>;
}

export interface DashboardLayout {
  id: string;
  theme: ThemeMode;
  layout: Array<{ i: string; x: number; y: number; w: number; h: number; minW?: number; minH?: number }>;
  updated_at?: string | null;
}

export interface AlertEvent {
  id: number;
  strategy_id: string;
  symbol: string;
  interval: string;
  signal: string;
  severity: string;
  message: string;
  detail: Record<string, unknown>;
  candle_open_time_ms: number | null;
  close_price: number | null;
  source: string;
  source_role: string;
  created_at: string;
}

export interface NewsEvent {
  id: number;
  source_type: string;
  source_name: string;
  published_at_utc: string;
  title: string;
  translated_title: string;
  speaker: string;
  content: string;
  translated_summary: string;
  url: string;
  metadata: Record<string, unknown>;
  first_seen_utc: string;
  notification_sent: boolean;
}

export interface NewsTranslateResult {
  requested: number;
  found: number;
  updated: number;
  unchanged: number;
}

export interface SourceHealth {
  source_name: string;
  label: string;
  status: string;
  last_success_utc: string | null;
  last_error_utc: string | null;
  last_error_message: string | null;
}

export interface Kline {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source: string;
  source_role: string;
}

export interface Snapshot {
  symbols: SymbolItem[];
  strategies: StrategyConfig[];
  modules: DashboardModule[];
  layout: DashboardLayout;
  alerts: AlertEvent[];
  news: NewsEvent[];
  health: SourceHealth[];
}

export interface WhaleTarget {
  id: string;
  label: string;
  address_or_subject: string;
  enabled: boolean;
  config: Record<string, any>;
  updated_at: string;
}

export interface WhaleTargetUpsert {
  id?: string | null;
  label: string;
  address_or_subject: string;
  enabled: boolean;
  config: Record<string, any>;
}

export interface WhaleAddressCandidate {
  address: string;
  label: string;
  source: string;
  chain: string;
  url?: string | null;
  confidence: number;
  target_id?: string | null;
}

export interface WhaleAddressResolveResponse {
  query: string;
  candidates: WhaleAddressCandidate[];
}

export interface WhaleDetail {
  target: WhaleTarget;
  recent_events: Array<Record<string, any>>;
  positions: Array<Record<string, any>>;
  holdings: Array<Record<string, any>>;
  defi_positions: Array<Record<string, any>>;
  open_orders: Array<Record<string, any>>;
  fills: Array<Record<string, any>>;
  historical_orders: Array<Record<string, any>>;
  funding: Array<Record<string, any>>;
  ledger_updates: Array<Record<string, any>>;
  portfolio: Array<Record<string, any>>;
  account_summary: Record<string, any>;
  snapshot: Record<string, any>;
  updated_at?: string | null;
}
