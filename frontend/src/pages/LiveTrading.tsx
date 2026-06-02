// Realtime trading view (WS-driven). Positions, orders, fills and signals live.
import StrategyPlanView from "@/components/StrategyPlanView";
import { useRealtime } from "@/store/realtime";
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

export default function LiveTrading() {
  const { positions, orders, fills, signals } = useRealtime();
  const posList = Object.values(positions);

  return (
    <div className="space-y-5">
      <StrategyPlanView />
      <Card>
        <CardTitle right={<Badge tone="accent">{posList.length} open</Badge>}>
          Open positions
        </CardTitle>
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
