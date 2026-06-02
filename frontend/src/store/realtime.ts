// Zustand store holding the latest realtime state, fed by the WebSocket client.
// Components read slices of this; the DB is never involved on this path.

import { create } from "zustand";
import { realtime, type RealtimeEvent, type Status } from "@/lib/ws";

const MAX_FEED = 200;
const MAX_CURVE = 1000;
const MAX_CANDLES_PER_KEY = 1500;

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

interface RealtimeState {
  status: Status;
  positions: Record<string, PositionRow>;
  equity: RealtimeEvent | null;
  equityCurve: { ts: number; equity: number }[];
  runEquity: Record<string, { ts: number; equity: number }[]>;
  fills: RealtimeEvent[];
  orders: RealtimeEvent[];
  signals: RealtimeEvent[];
  errors: RealtimeEvent[];
  runs: RealtimeEvent[];
  market: Record<string, RealtimeEvent>;
  candles: Record<string, Record<string, RealtimeEvent[]>>;
  tradeLevels: Record<string, RealtimeEvent>;
  symbolSummaries: Record<string, RealtimeEvent>;
  setStatus: (s: Status) => void;
  ingest: (e: RealtimeEvent) => void;
  reset: () => void;
}

function prepend(list: RealtimeEvent[], e: RealtimeEvent): RealtimeEvent[] {
  return [e, ...list].slice(0, MAX_FEED);
}

function candleKey(e: RealtimeEvent): string {
  return String(e.interval ?? "1m");
}

function candleTime(e: RealtimeEvent): string {
  return String(e.open_time ?? e.ts ?? "");
}

function appendCandle(
  candles: Record<string, Record<string, RealtimeEvent[]>>,
  e: RealtimeEvent,
): Record<string, Record<string, RealtimeEvent[]>> {
  const symbol = String(e.symbol ?? "");
  if (!symbol) return candles;
  const interval = candleKey(e);
  const current = candles[symbol]?.[interval] ?? [];
  const incomingTime = candleTime(e);
  const withoutSame = current.filter((row) => candleTime(row) !== incomingTime);
  const next = [...withoutSame, e]
    .sort((a, b) => new Date(candleTime(a)).getTime() - new Date(candleTime(b)).getTime())
    .slice(-MAX_CANDLES_PER_KEY);
  return {
    ...candles,
    [symbol]: {
      ...(candles[symbol] ?? {}),
      [interval]: next,
    },
  };
}

export const useRealtime = create<RealtimeState>((set) => ({
  status: "closed",
  positions: {},
  equity: null,
  equityCurve: [],
  runEquity: {},
  fills: [],
  orders: [],
  signals: [],
  errors: [],
  runs: [],
  market: {},
  candles: {},
  tradeLevels: {},
  symbolSummaries: {},
  setStatus: (s) => set({ status: s }),
  reset: () =>
    set({
      positions: {},
      equity: null,
      equityCurve: [],
      runEquity: {},
      fills: [],
      orders: [],
      signals: [],
      errors: [],
      runs: [],
      market: {},
      candles: {},
      tradeLevels: {},
      symbolSummaries: {},
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
          const rid = e.run_id ? String(e.run_id) : "";
          const runEquity = { ...state.runEquity };
          if (rid) {
            const prev = runEquity[rid] ?? [];
            runEquity[rid] = [...prev, { ts, equity: eq }].slice(-MAX_CURVE);
          }
          return { equity: e, equityCurve: curve, runEquity };
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
          return symbol ? { market: { ...state.market, [symbol]: e } } : {};
        }
        case "candle":
          return { candles: appendCandle(state.candles, e) };
        case "trade_level": {
          const symbol = String(e.symbol ?? "");
          return symbol ? { tradeLevels: { ...state.tradeLevels, [symbol]: e } } : {};
        }
        case "symbol_summary": {
          const symbol = String(e.symbol ?? "");
          return symbol ? { symbolSummaries: { ...state.symbolSummaries, [symbol]: e } } : {};
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
    "symbol_summary",
  ]);
}
