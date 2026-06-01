// Zustand store holding the latest realtime state, fed by the WebSocket client.
// Components read slices of this; the DB is never involved on this path.

import { create } from "zustand";
import { realtime, type RealtimeEvent, type Status } from "@/lib/ws";

const MAX_FEED = 200;
const MAX_CURVE = 1000;

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
  fills: RealtimeEvent[];
  orders: RealtimeEvent[];
  signals: RealtimeEvent[];
  errors: RealtimeEvent[];
  runs: RealtimeEvent[];
  setStatus: (s: Status) => void;
  ingest: (e: RealtimeEvent) => void;
  reset: () => void;
}

function prepend(list: RealtimeEvent[], e: RealtimeEvent): RealtimeEvent[] {
  return [e, ...list].slice(0, MAX_FEED);
}

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
  setStatus: (s) => set({ status: s }),
  reset: () =>
    set({ positions: {}, equity: null, equityCurve: [], fills: [], orders: [], signals: [], errors: [], runs: [] }),
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
  realtime.subscribe(["order", "fill", "position", "signal", "equity", "error", "run"]);
}
