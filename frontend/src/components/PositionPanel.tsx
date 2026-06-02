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
              </span>
            </div>
          </div>
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
          <div className="flex items-center justify-between py-1 text-sm">
            <span className="text-muted">Take profit</span>
            <span className="num text-up">{level?.take_profit ? usd(level.take_profit) : "—"}</span>
          </div>
          <div className="flex items-center justify-between py-1 text-sm">
            <span className="text-muted">Stop loss</span>
            <span className="num text-down">{level?.stop_loss ? usd(level.stop_loss) : "—"}</span>
          </div>
        </div>
      </Card>
    </div>
  );
}
