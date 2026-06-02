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

export interface RunRow {
  id: string;
  strategy: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
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
  startRun: (b: unknown) => api.post<{ run_id: string; mode: string }>("/control/start", b),
  stopRun: (runId: string) => api.post<{ run_id: string }>(`/control/stop/${runId}`),
  klines: (args: { symbol: string; interval: string; limit?: number }) => {
    const params = new URLSearchParams({ symbol: args.symbol, interval: args.interval });
    if (args.limit) params.set("limit", String(args.limit));
    return api.get<Kline[]>(`/market/klines?${params.toString()}`);
  },
};

export interface Kline {
  t: number;
  o: string;
  h: string;
  l: string;
  c: string;
  v: string;
}
