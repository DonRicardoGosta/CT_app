// Thin REST client for the history/config/control endpoints (DB-backed paths).
// Realtime data does NOT go through here — see lib/ws.ts.

const BASE = "/api";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

// ---- Typed endpoint helpers ------------------------------------------------
export type StrategySchemas = Record<string, JsonSchema>;
export interface JsonSchema {
  title?: string;
  properties?: Record<string, JsonSchemaProp>;
  required?: string[];
}
export interface JsonSchemaProp {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
}

export interface ApiKeyPublic {
  id: number;
  name: string;
  exchange: string;
  api_key_masked: string;
  is_active: boolean;
  created_at: string | null;
}

export interface RiskConfig {
  id: number;
  name: string;
  max_capital_usd: string;
  max_loss_usd: string;
  min_investment_usd: string;
  base_leverage: number;
  max_leverage: number;
  leverage_step: number;
  allow_hedge: boolean;
  fee_rate: string;
}

export interface RunConfig {
  mode?: string;
  strategy?: string;
  params?: Record<string, unknown>;
  risk?: Record<string, unknown>;
  symbols?: string[];
  interval?: string;
  initial_capital?: string | number;
  api_key_id?: number | null;
  [key: string]: unknown;
}

export interface RunRow {
  id: string;
  strategy: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  config: RunConfig | null;
  summary: Record<string, unknown>;
}

export interface LogRow {
  ts: string;
  severity: "debug" | "info" | "warn" | "warning" | "error" | "critical" | string;
  source: string;
  message: string;
  run_id?: string | null;
  mode?: string | null;
  symbol?: string | null;
  kind: string;
  context: Record<string, unknown>;
}

export interface LogFilters {
  runId?: string;
  mode?: string;
  severity?: string;
  source?: string;
  q?: string;
  limit?: number;
}

export const endpoints = {
  strategies: () => api.get<StrategySchemas>("/config/strategies"),
  apiKeys: () => api.get<ApiKeyPublic[]>("/config/api-keys"),
  createApiKey: (b: { name: string; exchange: string; api_key: string; secret: string }) =>
    api.post<ApiKeyPublic>("/config/api-keys", b),
  deleteApiKey: (id: number) => api.del<void>(`/config/api-keys/${id}`),
  testApiKey: (id: number) => api.post<{ ok: boolean }>(`/config/api-keys/${id}/test`),
  riskConfigs: () => api.get<RiskConfig[]>("/config/risk-configs"),
  createRiskConfig: (b: Partial<RiskConfig>) => api.post<RiskConfig>("/config/risk-configs", b),
  updateRiskConfig: (id: number, b: Partial<RiskConfig>) =>
    api.put<RiskConfig>(`/config/risk-configs/${id}`, b),
  deleteRiskConfig: (id: number) => api.del<void>(`/config/risk-configs/${id}`),
  runs: () => api.get<RunRow[]>("/history/runs"),
  orders: (runId?: string) => api.get<any[]>(`/history/orders${runId ? `?run_id=${runId}` : ""}`),
  fills: (runId?: string) => api.get<any[]>(`/history/fills${runId ? `?run_id=${runId}` : ""}`),
  equity: (runId: string) => api.get<any[]>(`/history/equity?run_id=${runId}`),
  errors: () => api.get<any[]>("/history/errors"),
  logs: (filters: LogFilters = {}) => {
    const params = new URLSearchParams();
    if (filters.runId) params.set("run_id", filters.runId);
    if (filters.mode) params.set("mode", filters.mode);
    if (filters.severity) params.set("severity", filters.severity);
    if (filters.source) params.set("source", filters.source);
    if (filters.q) params.set("q", filters.q);
    if (filters.limit) params.set("limit", String(filters.limit));
    const query = params.toString();
    return api.get<LogRow[]>(`/history/logs${query ? `?${query}` : ""}`);
  },
  startRun: (b: unknown) => api.post<{ run_id: string; mode: string }>("/control/start", b),
  stopRun: (runId: string) => api.post<{ run_id: string }>(`/control/stop/${runId}`),
  accountBalance: (apiKeyId?: number) =>
    api.get<AccountBalance>(`/account/balance${apiKeyId != null ? `?api_key_id=${apiKeyId}` : ""}`),
  systemMetrics: (range: string) => api.get<MetricSeries[]>(`/system/metrics?range=${range}`),
  systemStatus: () => api.get<MetricSnapshot[]>("/system/status"),
  klines: (args: { symbol: string; interval: string; limit?: number }) => {
    const params = new URLSearchParams({ symbol: args.symbol, interval: args.interval });
    if (args.limit) params.set("limit", String(args.limit));
    return api.get<Kline[]>(`/market/klines?${params.toString()}`);
  },
  tickers: (symbols?: string[]) => {
    const q = symbols?.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
    return api.get<Ticker[]>(`/market/tickers${q}`);
  },
  strategyConfigs: () => api.get<StrategyConfigRow[]>("/config/strategy-configs"),
  createStrategyConfig: (b: StrategyConfigIn) =>
    api.post<StrategyConfigRow>("/config/strategy-configs", b),
  deleteStrategyConfig: (id: number) => api.del<void>(`/config/strategy-configs/${id}`),
};

export interface Ticker {
  symbol: string;
  last: string | null;
  change_24h_pct: number | null;
}

export interface StrategyConfigIn {
  name: string;
  strategy: string;
  params: Record<string, unknown>;
  risk_config_id?: number | null;
  enabled?: boolean;
}

export interface StrategyConfigRow {
  id: number;
  name: string;
  strategy: string;
  params: Record<string, unknown>;
  risk_config_id: number | null;
  enabled: boolean;
}

export interface Kline {
  t: number;
  o: string;
  h: string;
  l: string;
  c: string;
  v: string;
}

export interface AccountBalance {
  margin_coin: string;
  balance: string | null;
  available: string | null;
  margin: string | null;
  unrealized_pnl: string | null;
  equity: string | null;
  raw: Record<string, unknown>;
}

export interface MetricPoint {
  ts: string;
  cpu_pct: number;
  mem_mb: number;
  mem_limit_mb: number | null;
}

export interface MetricSeries {
  service: string;
  points: MetricPoint[];
}

export interface MetricSnapshot {
  service: string;
  ts: string;
  cpu_pct: number;
  mem_mb: number;
  mem_limit_mb: number | null;
}
