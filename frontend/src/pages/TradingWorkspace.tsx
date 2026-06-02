// Live trading workspace: 5 coin cards, charts (WS), positions/orders below.
import { useMemo } from "react";
import { Link } from "react-router-dom";
import CoinChart, { type OhlcBar } from "@/components/CoinChart";
import { useRealtime } from "@/store/realtime";
import type { RealtimeEvent } from "@/lib/ws";
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

const DEFAULT_INTERVAL = "1m";

function parseCandles(rows: RealtimeEvent[] | undefined): OhlcBar[] {
  if (!rows?.length) return [];
  return rows
    .map((c) => ({
      t: Math.floor(new Date(String(c.open_time ?? c.ts)).getTime() / 1000),
      o: parseFloat(String(c.open)),
      h: parseFloat(String(c.high)),
      l: parseFloat(String(c.low)),
      c: parseFloat(String(c.close)),
    }))
    .filter((b) => b.t > 0 && isFinite(b.c));
}

function livePrice(symbol: string, market: Record<string, RealtimeEvent>, summary?: RealtimeEvent) {
  const tick = market[symbol];
  if (tick?.price != null) return parseFloat(String(tick.price));
  if (summary?.last_price != null) return parseFloat(String(summary.last_price));
  return NaN;
}

function SymbolPanel({
  symbol,
  summary,
  market,
  candles,
  tradeLevel,
}: {
  symbol: string;
  summary?: RealtimeEvent;
  market: Record<string, RealtimeEvent>;
  candles: OhlcBar[];
  tradeLevel?: RealtimeEvent;
}) {
  const price = livePrice(symbol, market, summary);
  const sl = tradeLevel?.stop_loss != null ? parseFloat(String(tradeLevel.stop_loss)) : undefined;
  const tp = tradeLevel?.take_profit != null ? parseFloat(String(tradeLevel.take_profit)) : undefined;
  const status = String(summary?.status ?? "waiting");
  const tone =
    status === "in_position" ? "up" : status === "pending_order" ? "warn" : "muted";

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold">{symbol}</span>
          <Badge tone={tone}>{status}</Badge>
          {summary?.position_side ? (
            <Badge tone={String(summary.position_side) === "long" ? "up" : "down"}>
              {String(summary.position_side)}
            </Badge>
          ) : null}
        </div>
        <span className="num text-xl font-semibold">{isFinite(price) ? usd(price) : "—"}</span>
      </div>

      {candles.length ? (
        <CoinChart
          bars={candles}
          stopLoss={isFinite(sl!) ? sl : undefined}
          takeProfit={isFinite(tp!) ? tp : undefined}
          height={200}
        />
      ) : (
        <Empty>Waiting for candles… (start dry-run / live)</Empty>
      )}

      <div className="grid grid-cols-2 gap-2 text-sm lg:grid-cols-4">
        <div>
          <div className="text-xs text-muted">Stop loss</div>
          <div className="num text-down">{tradeLevel?.stop_loss ? usd(tradeLevel.stop_loss) : "—"}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Take profit</div>
          <div className="num text-up">{tradeLevel?.take_profit ? usd(tradeLevel.take_profit) : "—"}</div>
        </div>
        <div>
          <div className="text-xs text-muted">Ladder</div>
          <div className="num">
            {summary?.step_count != null ? String(summary.step_count) : "—"} /{" "}
            {summary?.max_steps != null ? String(summary.max_steps) : "—"}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">uPnL</div>
          <div className={`num ${pnlClass(summary?.unrealized_pnl)}`}>
            {summary?.unrealized_pnl != null ? usd(summary.unrealized_pnl) : "—"}
          </div>
        </div>
      </div>
      {summary?.last_signal_reason ? (
        <p className="text-xs text-muted truncate">{String(summary.last_signal_reason)}</p>
      ) : null}
    </Card>
  );
}

export default function TradingWorkspace() {
  const {
    status,
    symbolSummaries,
    market,
    candles,
    tradeLevels,
    positions,
    orders,
    signals,
    fills,
  } = useRealtime();

  const symbols = useMemo(() => {
    const keys = new Set([
      ...Object.keys(symbolSummaries),
      ...Object.keys(market),
      ...Object.keys(candles),
      ...Object.keys(tradeLevels),
    ]);
    return [...keys].sort().slice(0, 5);
  }, [symbolSummaries, market, candles, tradeLevels]);

  const posList = Object.values(positions);

  return (
    <div className="space-y-5">
      {status !== "open" && (
        <Card className="border-warn/40 bg-warn/10">
          <p className="text-sm text-warn">
            WebSocket is <strong>{status}</strong> — live prices will not update. Check{" "}
            <Link to="/health" className="underline">
              System Health
            </Link>{" "}
            and ensure backend-api + Kafka are running.
          </p>
        </Card>
      )}

      <Card>
        <CardTitle right={<Badge tone="accent">{symbols.length} symbols</Badge>}>
          Live coins
        </CardTitle>
        {!symbols.length ? (
          <Empty>
            No coin data yet. Start a <strong>dry-run</strong> or <strong>live</strong> run from
            Strategies — summaries and charts appear after the engine connects to Bitunix.
          </Empty>
        ) : (
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            {symbols.map((sym) => (
              <SymbolPanel
                key={sym}
                symbol={sym}
                summary={symbolSummaries[sym]}
                market={market}
                candles={parseCandles(candles[sym]?.[DEFAULT_INTERVAL])}
                tradeLevel={tradeLevels[sym]}
              />
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardTitle right={<Badge tone="accent">{posList.length} open</Badge>}>Open positions</CardTitle>
        {posList.length === 0 ? (
          <Empty>No open positions</Empty>
        ) : (
          <Table head={["Symbol", "Side", "Qty", "Entry", "Mark", "Lev", "Steps", "Margin", "uPnL"]}>
            {posList.map((p, i) => (
              <Tr key={i}>
                <Td className="font-medium">{p.symbol}</Td>
                <Td>
                  <Badge tone={p.position_side === "long" ? "up" : "down"}>{p.position_side}</Badge>
                </Td>
                <Td className="num">{num(p.qty, 4)}</Td>
                <Td className="num">{usd(p.entry_price)}</Td>
                <Td className="num">{usd(p.mark_price)}</Td>
                <Td className="num">{p.leverage}x</Td>
                <Td className="num">{p.step_count}</Td>
                <Td className="num">{usd(p.margin)}</Td>
                <Td className={`num ${pnlClass(p.unrealized_pnl)}`}>{usd(p.unrealized_pnl)}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>Order stream</CardTitle>
          {orders.length === 0 ? (
            <Empty>No orders yet</Empty>
          ) : (
            <Table head={["Time", "Symbol", "Side", "Qty", "Status", "Lev"]}>
              {orders.slice(0, 15).map((o, i) => (
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
          )}
        </Card>

        <Card>
          <CardTitle>Signals</CardTitle>
          {signals.length === 0 ? (
            <Empty>No signals yet</Empty>
          ) : (
            <Table head={["Time", "Symbol", "Action", "Side", "Reason"]}>
              {signals.slice(0, 15).map((s, i) => (
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

      <Card>
        <CardTitle>Fills</CardTitle>
        {fills.length === 0 ? (
          <Empty>No fills yet</Empty>
        ) : (
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
        )}
      </Card>
    </div>
  );
}
