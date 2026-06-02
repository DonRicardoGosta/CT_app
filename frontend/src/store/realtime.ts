// Zustand store holding the latest realtime state, fed by the WebSocket client.
// Components read slices of this; the DB is never involved on this path.

import { create } from "zustand";
import { realtime, type RealtimeEvent, type Status } from "@/lib/ws";

const MAX_FEED = 200;
const MAX_CURVE = 1000;
const MAX_LIVE_CANDLES = 1500;

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

interface RealtimeState {
  status: Status;
  positions: Record<string, PositionRow>;
  equity: RealtimeEvent | null;
  equityCurve: { ts: number; equity: number }[];
  fills: RealtimeEvent[];
  orders: RealtimeEvent[];
  signals: RealtimeEvent[];
  errors: RealtimeEvent[];
  runs: RealtimeEvent[];
  // Trading workspace state
  watchlist: string[];
  watchScanning: string[];
  watchTarget: number;
  watchComplete: boolean;
  watchInterval: string;
  prices: Record<string, number>;
  tradeLevels: Record<string, TradeLevel>;
  symbolSummaries: Record<string, SymbolSummary>;
  liveCandles: Record<string, LiveCandle[]>; // keyed by symbol (run interval)
  setStatus: (s: Status) => void;
  ingest: (e: RealtimeEvent) => void;
  reset: () => void;
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

const EMPTY_WORKSPACE = {
  watchlist: [] as string[],
  watchScanning: [] as string[],
  watchTarget: 0,
  watchComplete: false,
  watchInterval: "1m",
  prices: {} as Record<string, number>,
  tradeLevels: {} as Record<string, TradeLevel>,
  symbolSummaries: {} as Record<string, SymbolSummary>,
  liveCandles: {} as Record<string, LiveCandle[]>,
};

export const useRealtime = create<RealtimeState>((set) => ({
  status: "closed",
  positions: {},
  equity: null,
  equityCurve: [],
  fills: [],
  orders: [],
  signals: [],
  errors: [],
  runs: [],
  ...EMPTY_WORKSPACE,
  setStatus: (s) => set({ status: s }),
  reset: () =>
    set({
      positions: {},
      equity: null,
      equityCurve: [],
      fills: [],
      orders: [],
      signals: [],
      errors: [],
      runs: [],
      ...EMPTY_WORKSPACE,
    }),
  ingest: (e) =>
    set((state) => {
      switch (e.type) {
        case "position": {
          const key = `${e.symbol}|${e.position_side}`;
          const positions = { ...state.positions };
          if (parseFloat(String(e.qty)) <= 0) delete positions[key];
          else positions[key] = e as unknown as PositionRow;
          return { positions };
        }
        case "equity": {
          const eq = parseFloat(String(e.equity));
          const ts = new Date(String(e.ts)).getTime();
          const curve = [...state.equityCurve, { ts, equity: eq }].slice(-MAX_CURVE);
          return { equity: e, equityCurve: curve };
        }
        case "fill":
          return { fills: prepend(state.fills, e) };
        case "order":
          return { orders: prepend(state.orders, e) };
        case "signal":
          return { signals: prepend(state.signals, e) };
        case "error":
          return { errors: prepend(state.errors, e) };
        case "run":
          return { runs: prepend(state.runs, e) };
        case "market": {
          const symbol = String(e.symbol ?? "");
          const price = parseFloat(String(e.price));
          if (!symbol || !isFinite(price)) return {};
          return { prices: { ...state.prices, [symbol]: price } };
        }
        case "candle": {
          const symbol = String(e.symbol ?? "");
          if (!symbol) return {};
          return {
            liveCandles: {
              ...state.liveCandles,
              [symbol]: appendCandle(state.liveCandles[symbol], e),
            },
          };
        }
        case "trade_level": {
          const symbol = String(e.symbol ?? "");
          if (!symbol) return {};
          const prev = state.tradeLevels[symbol] ?? { symbol };
          // Merge so a price-only update doesn't wipe entry/tp/sl.
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
          if (Array.isArray(e.take_profits)) {
            merged.take_profits = (e.take_profits as unknown[]).map(String);
          }
          if (Array.isArray(e.stops)) {
            merged.stops = (e.stops as unknown[]).map(String);
          }
          return { tradeLevels: { ...state.tradeLevels, [symbol]: merged } };
        }
        case "watchlist": {
          const symbols = (e.symbols as string[]) ?? [];
          const scanning = (e.scanning as string[]) ?? [];
          const target = Number(e.target ?? symbols.length);
          return {
            watchlist: symbols,
            watchScanning: scanning,
            watchTarget: target,
            watchComplete: Boolean(e.complete),
            watchInterval: String(e.interval ?? "1m"),
          };
        }
        case "symbol_summary": {
          const symbol = String(e.symbol ?? "");
          if (!symbol) return {};
          return {
            symbolSummaries: {
              ...state.symbolSummaries,
              [symbol]: e as unknown as SymbolSummary,
            },
          };
        }
        default:
          return {};
      }
    }),
}));

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
