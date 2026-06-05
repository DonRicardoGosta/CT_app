// Chart-centric live/dry/backtest workspace: 5 coins, one big selectable chart
// with interval switch and price/entry/TP/SL overlays, plus position + feeds.
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import BigChart, { type Candle, type ChartLevels } from "@/components/BigChart";
import MiniCoinCard from "@/components/MiniCoinCard";
import IntervalSelector, { type Interval } from "@/components/IntervalSelector";
import PositionPanel from "@/components/PositionPanel";
import { useMode, useRealtime, type TradeMode } from "@/store/realtime";
import { endpoints } from "@/lib/api";
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

type FeedTab = "orders" | "fills" | "signals";

function num0(v: unknown): number {
  return parseFloat(String(v ?? ""));
}

export default function TradingWorkspace({ mode = "live" }: { mode?: TradeMode }) {
  const status = useRealtime((s) => s.status);
  const bucket = useMode(mode);
  const {
    watchlist,
    watchScanning,
    watchTarget,
    watchComplete,
    watchInterval,
    prices,
    tradeLevels,
    symbolSummaries,
    liveCandles,
    positions,
    orders,
    fills,
    signals,
  } = bucket;

  // Selected (tradeable) coins. Fall back to whatever we've seen if a strategy
  // does not publish a watchlist.
  const symbols = useMemo(() => {
    if (watchlist.length) return watchlist;
    const seen = new Set([
      ...Object.keys(symbolSummaries),
      ...Object.keys(prices),
      ...Object.keys(liveCandles),
    ]);
    return [...seen].sort().slice(0, watchTarget || 5);
  }, [watchlist, symbolSummaries, prices, liveCandles, watchTarget]);

  const target = watchTarget || symbols.length || 5;
  const runActive = symbols.length > 0 || watchScanning.length > 0 || watchTarget > 0;
  const placeholders = Math.max(0, target - symbols.length);

  const [active, setActive] = useState<string>("");
  const [interval, setInterval] = useState<Interval>("5m");
  const [tab, setTab] = useState<FeedTab>("orders");

  // Keep an active symbol selected as soon as we know the watchlist.
  useEffect(() => {
    if (!active && symbols.length) setActive(symbols[0]);
  }, [symbols, active]);

  const activeSymbol = active || symbols[0] || "";

  // True 24h change from the exchange ticker (not the loaded candle window).
  const tickers = useQuery({
    queryKey: ["tickers", symbols],
    queryFn: () => endpoints.tickers(symbols),
    enabled: symbols.length > 0,
    refetchInterval: 15000,
    staleTime: 10000,
  });
  const change24hBySymbol = useMemo(() => {
    const map: Record<string, number> = {};
    for (const t of tickers.data ?? []) {
      if (t.change_24h_pct != null && isFinite(t.change_24h_pct)) {
        map[t.symbol] = t.change_24h_pct;
      }
    }
    return map;
  }, [tickers.data]);

  const klines = useQuery({
    queryKey: ["klines", activeSymbol, interval],
    queryFn: () => endpoints.klines({ symbol: activeSymbol, interval, limit: 300 }),
    enabled: !!activeSymbol,
    refetchInterval: 30000,
    staleTime: 20000,
  });

  // Base candles from REST; overlay live candles when the run interval matches.
  const candles: Candle[] = useMemo(() => {
    const base: Candle[] = (klines.data ?? []).map((k) => ({
      t: k.t,
      o: num0(k.o),
      h: num0(k.h),
      l: num0(k.l),
      c: num0(k.c),
    }));
    if (interval === watchInterval && liveCandles[activeSymbol]?.length) {
      const map = new Map<number, Candle>();
      for (const b of base) map.set(b.t, b);
      for (const b of liveCandles[activeSymbol]) map.set(b.t, b);
      return [...map.values()].sort((a, b) => a.t - b.t);
    }
    return base;
  }, [klines.data, liveCandles, activeSymbol, interval, watchInterval]);

  const level = tradeLevels[activeSymbol];
  const livePrice = prices[activeSymbol];
  const tpList = (level?.take_profits?.length
    ? level.take_profits
    : level?.take_profit
      ? [level.take_profit]
      : []
  )
    .map(num0)
    .filter((n) => isFinite(n));
  const slList = (level?.stops?.length
    ? level.stops
    : level?.stop_loss
      ? [level.stop_loss]
      : []
  )
    .map(num0)
    .filter((n) => isFinite(n));
  const chartLevels: ChartLevels = {
    price: livePrice ?? (level?.current_price ? num0(level.current_price) : undefined),
    entry: level?.actual_entry
      ? num0(level.actual_entry)
      : level?.planned_entry
        ? num0(level.planned_entry)
        : undefined,
    takeProfits: tpList,
    stops: slList,
  };

  const activePosition = useMemo(() => {
    const list = Object.values(positions).filter((p) => p.symbol === activeSymbol);
    return list.find((p) => num0(p.qty) > 0) ?? list[0];
  }, [positions, activeSymbol]);

  const summary = symbolSummaries[activeSymbol];
  // 24h change comes from the ticker field; fall back to the loaded candle
  // window only if the ticker has not loaded yet.
  const change = useMemo(() => {
    const t = change24hBySymbol[activeSymbol];
    if (t != null && isFinite(t)) return t;
    if (candles.length < 2) return null;
    const first = candles[0].c;
    const last = livePrice ?? candles[candles.length - 1].c;
    if (!first) return null;
    return ((last - first) / first) * 100;
  }, [change24hBySymbol, activeSymbol, candles, livePrice]);

  if (!runActive) {
    return (
      <Card>
        <CardTitle
          right={<Badge tone={status === "open" ? "up" : "down"}>WS {status}</Badge>}
        >
          Trading workspace
        </CardTitle>
        <Empty>
          No active run. Start a <strong>dry-run</strong>, <strong>live</strong> or{" "}
          <strong>backtest</strong> from{" "}
          <Link to="/strategies" className="text-accent underline">
            Strategies
          </Link>{" "}
          — the selected coins, charts and levels appear here.
        </Empty>
      </Card>
    );
  }

  // Scanning but nothing selected yet.
  if (!symbols.length) {
    return (
      <Card>
        <CardTitle
          right={<Badge tone="warn">Selecting coins (0/{target})</Badge>}
        >
          Scanning for tradeable coins
        </CardTitle>
        <Empty>
          Searching {watchScanning.length || "the"} candidates for valid setups — coins
          appear here as soon as the strategy can open a position in them.
        </Empty>
      </Card>
    );
  }

  const feed = tab === "orders" ? orders : tab === "fills" ? fills : signals;

  return (
    <div className="space-y-4">
      {status !== "open" && (
        <Card className="border-warn/40 bg-warn/10">
          <p className="text-sm text-warn">
            WebSocket is <strong>{status}</strong> — live prices will not update. Check{" "}
            <Link to="/health" className="underline">
              System Health
            </Link>
            .
          </p>
        </Card>
      )}

      {/* Coin selection status */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-text">Coins</span>
        {watchComplete ? (
          <Badge tone="up">{symbols.length} selected</Badge>
        ) : (
          <Badge tone="warn">
            Selecting coins ({symbols.length}/{target})
            {watchScanning.length ? ` · scanning ${watchScanning.length}` : ""}
          </Badge>
        )}
      </div>

      {/* Coin strip: selected coins + searching placeholders */}
      <div className="flex flex-wrap gap-3">
        {symbols.map((sym) => {
          const spark = (liveCandles[sym] ?? []).map((b) => b.c);
          return (
            <MiniCoinCard
              key={sym}
              symbol={sym}
              active={sym === activeSymbol}
              price={prices[sym]}
              change24h={change24hBySymbol[sym]}
              spark={spark}
              summary={symbolSummaries[sym]}
              onClick={() => setActive(sym)}
            />
          );
        })}
        {Array.from({ length: placeholders }).map((_, i) => (
          <div
            key={`ph${i}`}
            className="flex min-w-[150px] flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border bg-panel/40 p-3 text-center"
          >
            <span className="text-sm font-medium text-muted">Searching…</span>
            <span className="mt-1 text-xs text-muted">slot {symbols.length + i + 1}/{target}</span>
          </div>
        ))}
      </div>

      {/* Main chart + position panel */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_320px]">
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-baseline gap-3">
              <span className="text-xl font-semibold">{activeSymbol}</span>
              <span className="num text-lg">{livePrice != null ? usd(livePrice) : "—"}</span>
              {change != null && (
                <span className={`text-sm ${change >= 0 ? "text-up" : "text-down"}`}>
                  {change >= 0 ? "+" : ""}
                  {change.toFixed(2)}%
                  <span className="ml-1 text-xs text-muted">24h</span>
                </span>
              )}
              {summary && <Badge tone="muted">{summary.status}</Badge>}
            </div>
            <IntervalSelector value={interval} onChange={setInterval} />
          </div>
          {klines.isError ? (
            <Empty>Could not load candles for {activeSymbol}. Is the backend reachable?</Empty>
          ) : candles.length ? (
            <BigChart candles={candles} levels={chartLevels} height={440} />
          ) : (
            <Empty>Loading candles…</Empty>
          )}
          <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted">
            <span>
              <span className="inline-block h-2 w-3 align-middle" style={{ background: "#3b82f6" }} />{" "}
              entry
            </span>
            <span>
              <span className="inline-block h-2 w-3 align-middle" style={{ background: "#16c784" }} />{" "}
              take profit
            </span>
            <span>
              <span className="inline-block h-2 w-3 align-middle" style={{ background: "#ea3943" }} />{" "}
              stop loss
            </span>
            <span>
              <span className="inline-block h-2 w-3 align-middle" style={{ background: "#9aa7b8" }} />{" "}
              price
            </span>
          </div>
        </Card>

        <PositionPanel symbol={activeSymbol} position={activePosition} level={level} />
      </div>

      {/* Feeds */}
      <Card>
        <div className="mb-3 flex items-center gap-2">
          {(["orders", "fills", "signals"] as FeedTab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1 text-sm font-medium capitalize transition-colors ${
                tab === t ? "bg-accent text-white" : "bg-panel2 text-muted hover:text-text"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        {feed.length === 0 ? (
          <Empty>No {tab} yet</Empty>
        ) : tab === "orders" ? (
          <Table head={["Time", "Symbol", "Side", "Qty", "Status", "Lev"]}>
            {orders.slice(0, 20).map((o, i) => (
              <Tr key={i}>
                <Td className="text-muted">{time(o.ts)}</Td>
                <Td>{String(o.symbol)}</Td>
                <Td>{String(o.side)}</Td>
                <Td className="num">{num(o.qty, 4)}</Td>
                <Td>{String(o.status)}</Td>
                <Td className="num">{String(o.leverage)}x</Td>
              </Tr>
            ))}
          </Table>
        ) : tab === "fills" ? (
          <Table head={["Time", "Symbol", "Side", "Qty", "Price", "Fee", "PnL"]}>
            {fills.slice(0, 20).map((f, i) => (
              <Tr key={i}>
                <Td className="text-muted">{time(f.ts)}</Td>
                <Td>{String(f.symbol)}</Td>
                <Td>{String(f.side)}</Td>
                <Td className="num">{num(f.qty, 4)}</Td>
                <Td className="num">{usd(f.price)}</Td>
                <Td className="num text-muted">{usd(f.fee, 4)}</Td>
                <Td className={`num ${pnlClass(f.realized_pnl)}`}>{usd(f.realized_pnl)}</Td>
              </Tr>
            ))}
          </Table>
        ) : (
          <Table head={["Time", "Symbol", "Action", "Side", "Reason"]}>
            {signals.slice(0, 20).map((s, i) => (
              <Tr key={i}>
                <Td className="text-muted">{time(s.ts)}</Td>
                <Td>{String(s.symbol)}</Td>
                <Td>{String(s.action)}</Td>
                <Td>{String(s.side)}</Td>
                <Td className="max-w-xs truncate text-muted">{String(s.reason)}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
