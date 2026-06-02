// Chart-centric live/dry/backtest workspace: 5 coins, one big selectable chart
// with interval switch and price/entry/TP/SL overlays, plus position + feeds.
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import BigChart, { type Candle, type ChartLevels } from "@/components/BigChart";
import MiniCoinCard from "@/components/MiniCoinCard";
import IntervalSelector, { type Interval } from "@/components/IntervalSelector";
import PositionPanel from "@/components/PositionPanel";
import { useRealtime } from "@/store/realtime";
import { endpoints } from "@/lib/api";
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

type FeedTab = "orders" | "fills" | "signals";

function num0(v: unknown): number {
  return parseFloat(String(v ?? ""));
}

export default function TradingWorkspace() {
  const {
    status,
    watchlist,
    watchInterval,
    prices,
    tradeLevels,
    symbolSummaries,
    liveCandles,
    positions,
    orders,
    fills,
    signals,
  } = useRealtime();

  // Symbols to show: prefer the strategy watchlist, fall back to whatever we've seen.
  const symbols = useMemo(() => {
    if (watchlist.length) return watchlist.slice(0, 5);
    const seen = new Set([
      ...Object.keys(symbolSummaries),
      ...Object.keys(prices),
      ...Object.keys(liveCandles),
    ]);
    return [...seen].sort().slice(0, 5);
  }, [watchlist, symbolSummaries, prices, liveCandles]);

  const [active, setActive] = useState<string>("");
  const [interval, setInterval] = useState<Interval>("5m");
  const [tab, setTab] = useState<FeedTab>("orders");

  // Keep an active symbol selected as soon as we know the watchlist.
  useEffect(() => {
    if (!active && symbols.length) setActive(symbols[0]);
  }, [symbols, active]);

  const activeSymbol = active || symbols[0] || "";

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
  const chartLevels: ChartLevels = {
    price: livePrice ?? (level?.current_price ? num0(level.current_price) : undefined),
    entry: level?.actual_entry
      ? num0(level.actual_entry)
      : level?.planned_entry
        ? num0(level.planned_entry)
        : undefined,
    takeProfit: level?.take_profit ? num0(level.take_profit) : undefined,
    stopLoss: level?.stop_loss ? num0(level.stop_loss) : undefined,
  };

  const activePosition = useMemo(() => {
    const list = Object.values(positions).filter((p) => p.symbol === activeSymbol);
    return list.find((p) => num0(p.qty) > 0) ?? list[0];
  }, [positions, activeSymbol]);

  const summary = symbolSummaries[activeSymbol];
  const change = useMemo(() => {
    if (candles.length < 2) return null;
    const first = candles[0].c;
    const last = livePrice ?? candles[candles.length - 1].c;
    if (!first) return null;
    return ((last - first) / first) * 100;
  }, [candles, livePrice]);

  if (!symbols.length) {
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
          — the 5 selected coins, charts and levels appear here.
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

      {/* Coin strip */}
      <div className="flex flex-wrap gap-3">
        {symbols.map((sym) => {
          const spark = (liveCandles[sym] ?? []).map((b) => b.c);
          const restSpark = spark.length ? spark : [];
          return (
            <MiniCoinCard
              key={sym}
              symbol={sym}
              active={sym === activeSymbol}
              price={prices[sym]}
              spark={restSpark}
              summary={symbolSummaries[sym]}
              onClick={() => setActive(sym)}
            />
          );
        })}
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
