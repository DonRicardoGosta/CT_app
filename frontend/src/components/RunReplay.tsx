// Read-only replay of a finished run from the database (equity curve + feeds).
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import EquityChart from "@/components/EquityChart";
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

type FeedTab = "orders" | "fills" | "signals";

export default function RunReplay({ runId }: { runId: string }) {
  const [tab, setTab] = useState<FeedTab>("fills");

  const equity = useQuery({
    queryKey: ["replay-equity", runId],
    queryFn: () => endpoints.equity(runId),
  });
  const orders = useQuery({
    queryKey: ["replay-orders", runId],
    queryFn: () => endpoints.orders(runId),
  });
  const fills = useQuery({
    queryKey: ["replay-fills", runId],
    queryFn: () => endpoints.fills(runId),
  });

  const points = useMemo(
    () =>
      (equity.data ?? []).map((p: any) => ({
        time: Math.floor(new Date(String(p.ts)).getTime() / 1000),
        value: parseFloat(String(p.equity)),
      })),
    [equity.data],
  );

  const fillRows = fills.data ?? [];
  const orderRows = orders.data ?? [];
  const realized = useMemo(
    () => fillRows.reduce((a: number, f: any) => a + parseFloat(String(f.realized_pnl ?? "0")), 0),
    [fillRows],
  );
  const lastEquity = points.length ? points[points.length - 1].value : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Final equity</span>
          <span className="num text-2xl font-semibold">
            {lastEquity != null ? usd(lastEquity) : "—"}
          </span>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Realized PnL</span>
          <span className={`num text-2xl font-semibold ${pnlClass(realized)}`}>{usd(realized)}</span>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Orders</span>
          <span className="num text-2xl font-semibold">{orderRows.length}</span>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Fills</span>
          <span className="num text-2xl font-semibold">{fillRows.length}</span>
        </Card>
      </div>

      <Card>
        <CardTitle right={<Badge tone="muted">replay</Badge>}>Equity curve</CardTitle>
        {points.length ? <EquityChart data={points} /> : <Empty>No equity data for this run</Empty>}
      </Card>

      <Card>
        <div className="mb-3 flex items-center gap-2">
          {(["fills", "orders"] as FeedTab[]).map((t) => (
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
        {tab === "fills" ? (
          fillRows.length === 0 ? (
            <Empty>No fills</Empty>
          ) : (
            <Table head={["Time", "Symbol", "Side", "Qty", "Price", "Fee", "PnL"]}>
              {fillRows.slice(0, 200).map((f: any, i: number) => (
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
          )
        ) : orderRows.length === 0 ? (
          <Empty>No orders</Empty>
        ) : (
          <Table head={["Time", "Symbol", "Side", "Qty", "Status", "Lev"]}>
            {orderRows.slice(0, 200).map((o: any, i: number) => (
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
    </div>
  );
}
