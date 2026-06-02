// Active-symbol position + levels detail panel.
import { Badge, Card, CardTitle, Empty } from "@/components/ui";
import type { PositionRow, TradeLevel } from "@/store/realtime";
import { num, pnlClass, usd } from "@/lib/format";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-1 text-sm">
      <span className="text-muted">{label}</span>
      <span className="num">{children}</span>
    </div>
  );
}

function pct(value: number): string {
  if (!isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function tone(value: number): string {
  if (!isFinite(value) || value === 0) return "text-muted";
  return value > 0 ? "text-up" : "text-down";
}

// PnL (USD) and return-on-margin (%) for moving ``entry -> target`` with ``qty``.
function pnlAt(
  side: string,
  entry: number,
  qty: number,
  margin: number,
  target: number,
): { usd: number; roe: number } {
  const dir = side === "long" ? 1 : -1;
  const pnl = (target - entry) * qty * dir;
  const roe = margin > 0 ? (pnl / margin) * 100 : NaN;
  return { usd: pnl, roe };
}

export default function PositionPanel({
  symbol,
  position,
  level,
}: {
  symbol: string;
  position?: PositionRow;
  level?: TradeLevel;
}) {
  return (
    <div className="space-y-4">
      <Card>
        <CardTitle
          right={
            position ? (
              <Badge tone={position.position_side === "long" ? "up" : "down"}>
                {position.position_side}
              </Badge>
            ) : (
              <Badge tone="muted">flat</Badge>
            )
          }
        >
          Position — {symbol}
        </CardTitle>
        {!position ? (
          <Empty>No open position</Empty>
        ) : (
          (() => {
            const side = position.position_side;
            const entry = parseFloat(position.entry_price);
            const qty = parseFloat(position.qty);
            const margin = parseFloat(position.margin);
            const upnl = parseFloat(position.unrealized_pnl);
            const roe = margin > 0 ? (upnl / margin) * 100 : NaN;

            const tps = level?.take_profits?.length
              ? level.take_profits.map(Number)
              : level?.take_profit
                ? [Number(level.take_profit)]
                : [];
            const stops = level?.stops?.length
              ? level.stops.map(Number)
              : level?.stop_loss
                ? [Number(level.stop_loss)]
                : [];
            // Furthest favourable TP and the widest (worst) stop.
            const bestTarget = tps.length
              ? side === "long"
                ? Math.max(...tps)
                : Math.min(...tps)
              : null;
            const worstStop = stops.length
              ? side === "long"
                ? Math.min(...stops)
                : Math.max(...stops)
              : null;
            const best =
              bestTarget != null ? pnlAt(side, entry, qty, margin, bestTarget) : null;
            const worst =
              worstStop != null ? pnlAt(side, entry, qty, margin, worstStop) : null;

            return (
              <div className="divide-y divide-border/50">
                <Row label="Qty">{num(position.qty, 4)}</Row>
                <Row label="Entry">{usd(position.entry_price)}</Row>
                <Row label="Mark">{usd(position.mark_price)}</Row>
                <Row label="Leverage">{position.leverage}x</Row>
                <Row label="Ladder steps">{position.step_count}</Row>
                <Row label="Margin">{usd(position.margin)}</Row>
                <div className="flex items-center justify-between py-1 text-sm">
                  <span className="text-muted">uPnL</span>
                  <span className={`num ${pnlClass(position.unrealized_pnl)}`}>
                    {usd(position.unrealized_pnl)}
                    <span className={tone(roe)}> ({pct(roe)})</span>
                  </span>
                </div>
                {best && (
                  <div className="flex items-center justify-between py-1 text-sm">
                    <span className="text-muted">Max upside (final TP)</span>
                    <span className="num text-up">
                      {usd(best.usd)} ({pct(best.roe)})
                    </span>
                  </div>
                )}
                {worst && (
                  <div className="flex items-center justify-between py-1 text-sm">
                    <span className="text-muted">Worst case (stop)</span>
                    <span className="num text-down">
                      {usd(worst.usd)} ({pct(worst.roe)})
                    </span>
                  </div>
                )}
              </div>
            );
          })()
        )}
      </Card>

      <Card>
        <CardTitle>Levels</CardTitle>
        <div className="divide-y divide-border/50">
          <div className="flex items-center justify-between py-1 text-sm">
            <span className="text-muted">Entry</span>
            <span className="num text-accent">
              {level?.actual_entry
                ? usd(level.actual_entry)
                : level?.planned_entry
                  ? `${usd(level.planned_entry)} (plan)`
                  : "—"}
            </span>
          </div>

          {(() => {
            const tps = level?.take_profits?.length
              ? level.take_profits
              : level?.take_profit
                ? [level.take_profit]
                : [];
            if (!tps.length) {
              return (
                <div className="flex items-center justify-between py-1 text-sm">
                  <span className="text-muted">Take profit</span>
                  <span className="num text-up">—</span>
                </div>
              );
            }
            return tps.map((tp, i) => (
              <div key={`tp${i}`} className="flex items-center justify-between py-1 text-sm">
                <span className="text-muted">{tps.length > 1 ? `Take profit ${i + 1}` : "Take profit"}</span>
                <span className="num text-up">{usd(tp)}</span>
              </div>
            ));
          })()}

          {(() => {
            const stops = level?.stops?.length
              ? level.stops
              : level?.stop_loss
                ? [level.stop_loss]
                : [];
            if (!stops.length) {
              return (
                <div className="flex items-center justify-between py-1 text-sm">
                  <span className="text-muted">Stop loss</span>
                  <span className="num text-down">—</span>
                </div>
              );
            }
            return stops.map((sl, i) => (
              <div key={`sl${i}`} className="flex items-center justify-between py-1 text-sm">
                <span className="text-muted">{stops.length > 1 ? `Stop ${i + 1}` : "Stop loss"}</span>
                <span className="num text-down">{usd(sl)}</span>
              </div>
            ));
          })()}
        </div>
      </Card>
    </div>
  );
}
