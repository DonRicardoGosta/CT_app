// Zustand store holding the latest realtime state, fed by the WebSocket client.
// Components read slices of this; the DB is never involved on this path.
//
// Events are bucketed by trading mode (live / dry_run / backtest) so the Live and
// Dry pages stay fully separated. Cross-cutting consumers (Logs, Health) read the
// combined feed arrays kept at the top level.

import { create } from "zustand";
import { realtime, type RealtimeEvent, type Status } from "@/lib/ws";

const MAX_FEED = 200;
const MAX_CURVE = 1000;
const MAX_LIVE_CANDLES = 1500;

export type TradeMode = "live" | "dry_run" | "backtest";
const MODES: TradeMode[] = ["live", "dry_run", "backtest"];

export interface PositionRow {
  symbol: string;
  position_side: string;
  qty: string;
  entry_price: string;
  mark_price: string;
  leverage: number;
  margin: string;
  unrealized_pnl: string;
  realized_pnl: string;
  step_count: number;
  run_id?: string;
}

export interface TradeLevel {
  symbol: string;
  position_side?: string;
  current_price?: string;
  planned_entry?: string;
  actual_entry?: string;
  take_profit?: string;
  stop_loss?: string;
  take_profits?: string[];
  stops?: string[];
  source?: string;
}

export interface SymbolSummary {
  symbol: string;
  status: string;
  last_price?: string;
  position_side?: string;
  unrealized_pnl?: string;
  realized_pnl?: string;
  step_count?: number;
  max_steps?: number;
  last_signal_reason?: string;
}

export interface LiveCandle {
  t: number; // open time, seconds
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface ModeBucket {
  positions: Record<string, PositionRow>;
  equity: RealtimeEvent | null;
  equityCurve: { ts: number; equity: number }[];
  fills: RealtimeEvent[];
  orders: RealtimeEvent[];
  signals: RealtimeEvent[];
  errors: RealtimeEvent[];
  runs: RealtimeEvent[];
  watchlist: string[];
  watchScanning: string[];
  watchTarget: number;
  watchComplete: boolean;
  watchInterval: string;
  prices: Record<string, number>;
  tradeLevels: Record<string, TradeLevel>;
  symbolSummaries: Record<string, SymbolSummary>;
  liveCandles: Record<string, LiveCandle[]>;
}

interface RealtimeState {
  status: Status;
  // Most recent equity event across any mode (used by the global header badge).
  lastEquity: RealtimeEvent | null;
  byMode: Record<TradeMode, ModeBucket>;
  // Combined (all-mode) feeds for the Logs and Health pages.
  orders: RealtimeEvent[];
  fills: RealtimeEvent[];
  signals: RealtimeEvent[];
  errors: RealtimeEvent[];
  runs: RealtimeEvent[];
  setStatus: (s: Status) => void;
  ingest: (e: RealtimeEvent) => void;
  reset: () => void;
}

function emptyBucket(): ModeBucket {
  return {
    positions: {},
    equity: null,
    equityCurve: [],
    fills: [],
    orders: [],
    signals: [],
    errors: [],
    runs: [],
    watchlist: [],
    watchScanning: [],
    watchTarget: 0,
    watchComplete: false,
    watchInterval: "1m",
    prices: {},
    tradeLevels: {},
    symbolSummaries: {},
    liveCandles: {},
  };
}

function emptyByMode(): Record<TradeMode, ModeBucket> {
  return { live: emptyBucket(), dry_run: emptyBucket(), backtest: emptyBucket() };
}

function prepend(list: RealtimeEvent[], e: RealtimeEvent): RealtimeEvent[] {
  return [e, ...list].slice(0, MAX_FEED);
}

function appendCandle(list: LiveCandle[] | undefined, e: RealtimeEvent): LiveCandle[] {
  const t = Math.floor(new Date(String(e.open_time ?? e.ts)).getTime() / 1000);
  const bar: LiveCandle = {
    t,
    o: parseFloat(String(e.open)),
    h: parseFloat(String(e.high)),
    l: parseFloat(String(e.low)),
    c: parseFloat(String(e.close)),
  };
  if (!isFinite(bar.t) || !isFinite(bar.c)) return list ?? [];
  const withoutSame = (list ?? []).filter((b) => b.t !== t);
  return [...withoutSame, bar].sort((a, b) => a.t - b.t).slice(-MAX_LIVE_CANDLES);
}

function modeOf(e: RealtimeEvent): TradeMode {
  const m = String(e.mode ?? "");
  return (MODES as string[]).includes(m) ? (m as TradeMode) : "dry_run";
}

// Apply an event to a single mode bucket, returning a new bucket (or the same
// reference when nothing relevant changed).
function reduceBucket(b: ModeBucket, e: RealtimeEvent): ModeBucket {
  switch (e.type) {
    case "position": {
      const key = `${e.symbol}|${e.position_side}`;
      const positions = { ...b.positions };
      if (parseFloat(String(e.qty)) <= 0) delete positions[key];
      else positions[key] = e as unknown as PositionRow;
      return { ...b, positions };
    }
    case "equity": {
      const eq = parseFloat(String(e.equity));
      const ts = new Date(String(e.ts)).getTime();
      const curve = [...b.equityCurve, { ts, equity: eq }].slice(-MAX_CURVE);
      return { ...b, equity: e, equityCurve: curve };
    }
    case "fill":
      return { ...b, fills: prepend(b.fills, e) };
    case "order":
      return { ...b, orders: prepend(b.orders, e) };
    case "signal":
      return { ...b, signals: prepend(b.signals, e) };
    case "error":
      return { ...b, errors: prepend(b.errors, e) };
    case "run":
      return { ...b, runs: prepend(b.runs, e) };
    case "market": {
      const symbol = String(e.symbol ?? "");
      const price = parseFloat(String(e.price));
      if (!symbol || !isFinite(price)) return b;
      return { ...b, prices: { ...b.prices, [symbol]: price } };
    }
    case "candle": {
      const symbol = String(e.symbol ?? "");
      if (!symbol) return b;
      return {
        ...b,
        liveCandles: { ...b.liveCandles, [symbol]: appendCandle(b.liveCandles[symbol], e) },
      };
    }
    case "trade_level": {
      const symbol = String(e.symbol ?? "");
      if (!symbol) return b;
      const prev = b.tradeLevels[symbol] ?? { symbol };
      const merged: TradeLevel = { ...prev };
      for (const k of [
        "position_side",
        "current_price",
        "planned_entry",
        "actual_entry",
        "take_profit",
        "stop_loss",
        "source",
      ] as const) {
        if (e[k] != null) (merged as any)[k] = String(e[k]);
      }
      if (Array.isArray(e.take_profits)) merged.take_profits = (e.take_profits as unknown[]).map(String);
      if (Array.isArray(e.stops)) merged.stops = (e.stops as unknown[]).map(String);
      return { ...b, tradeLevels: { ...b.tradeLevels, [symbol]: merged } };
    }
    case "watchlist": {
      const symbols = (e.symbols as string[]) ?? [];
      const scanning = (e.scanning as string[]) ?? [];
      const target = Number(e.target ?? symbols.length);
      return {
        ...b,
        watchlist: symbols,
        watchScanning: scanning,
        watchTarget: target,
        watchComplete: Boolean(e.complete),
        watchInterval: String(e.interval ?? "1m"),
      };
    }
    case "symbol_summary": {
      const symbol = String(e.symbol ?? "");
      if (!symbol) return b;
      return {
        ...b,
        symbolSummaries: { ...b.symbolSummaries, [symbol]: e as unknown as SymbolSummary },
      };
    }
    default:
      return b;
  }
}

const COMBINED = new Set(["order", "fill", "signal", "error", "run"]);

export const useRealtime = create<RealtimeState>((set) => ({
  status: "closed",
  lastEquity: null,
  byMode: emptyByMode(),
  orders: [],
  fills: [],
  signals: [],
  errors: [],
  runs: [],
  setStatus: (s) => set({ status: s }),
  reset: () =>
    set({
      lastEquity: null,
      byMode: emptyByMode(),
      orders: [],
      fills: [],
      signals: [],
      errors: [],
      runs: [],
    }),
  ingest: (e) =>
    set((state) => {
      const mode = modeOf(e);
      const bucket = reduceBucket(state.byMode[mode], e);
      const patch: Partial<RealtimeState> = {};
      if (bucket !== state.byMode[mode]) {
        patch.byMode = { ...state.byMode, [mode]: bucket };
      }
      if (e.type === "equity") patch.lastEquity = e;
      // Mirror feed events into the combined arrays for Logs/Health.
      if (COMBINED.has(e.type)) {
        switch (e.type) {
          case "order":
            patch.orders = prepend(state.orders, e);
            break;
          case "fill":
            patch.fills = prepend(state.fills, e);
            break;
          case "signal":
            patch.signals = prepend(state.signals, e);
            break;
          case "error":
            patch.errors = prepend(state.errors, e);
            break;
          case "run":
            patch.runs = prepend(state.runs, e);
            break;
        }
      }
      return patch;
    }),
}));

// Hook: read a single mode's bucket.
export function useMode(mode: TradeMode): ModeBucket {
  return useRealtime((s) => s.byMode[mode]);
}

let wired = false;

// Wire the realtime client to the store exactly once.
export function initRealtime() {
  if (wired) return;
  wired = true;
  realtime.onStatus((s) => useRealtime.getState().setStatus(s));
  realtime.onEvent((e) => useRealtime.getState().ingest(e));
  realtime.connect();
  realtime.subscribe([
    "order",
    "fill",
    "position",
    "signal",
    "equity",
    "error",
    "run",
    "market",
    "candle",
    "trade_level",
    "watchlist",
    "symbol_summary",
  ]);
}
