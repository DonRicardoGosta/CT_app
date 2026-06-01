// History & analytics (DB-backed). Pick a run, inspect its equity and trades.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import EquityChart from "@/components/EquityChart";
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

function metrics(equity: any[], fills: any[]) {
  if (!equity.length) return null;
  const start = parseFloat(equity[0].equity);
  const end = parseFloat(equity[equity.length - 1].equity);
  const ret = start ? ((end - start) / start) * 100 : 0;
  let peak = -Infinity;
  let maxDd = 0;
  for (const e of equity) {
    const v = parseFloat(e.equity);
    peak = Math.max(peak, v);
    maxDd = Math.max(maxDd, peak ? ((peak - v) / peak) * 100 : 0);
  }
  const closed = fills.filter((f) => parseFloat(f.realized_pnl) !== 0);
  const wins = closed.filter((f) => parseFloat(f.realized_pnl) > 0).length;
  const winRate = closed.length ? (wins / closed.length) * 100 : 0;
  return { ret, maxDd, winRate, trades: closed.length };
}

export default function Analytics() {
  const { data: runs } = useQuery({ queryKey: ["runs"], queryFn: endpoints.runs });
  const [runId, setRunId] = useState<string>("");
  const active = runId || runs?.[0]?.id || "";

  const equity = useQuery({
    queryKey: ["equity", active],
    queryFn: () => endpoints.equity(active),
    enabled: !!active,
  });
  const fills = useQuery({
    queryKey: ["fills", active],
    queryFn: () => endpoints.fills(active),
    enabled: !!active,
  });

  const points = (equity.data ?? []).map((e: any) => ({
    time: Math.floor(new Date(e.ts).getTime() / 1000),
    value: parseFloat(e.equity),
  }));
  const m = metrics(equity.data ?? [], fills.data ?? []);

  return (
    <div className="space-y-5">
      <Card>
        <CardTitle>Runs</CardTitle>
        {!runs?.length ? (
          <Empty>No runs recorded yet</Empty>
        ) : (
          <Table head={["", "Strategy", "Mode", "Status", "Started"]}>
            {runs.map((r) => (
              <Tr key={r.id}>
                <Td>
                  <button
                    onClick={() => setRunId(r.id)}
                    className={`rounded px-2 py-0.5 text-xs ${
                      active === r.id ? "bg-accent text-white" : "bg-panel2 text-muted"
                    }`}
                  >
                    {active === r.id ? "selected" : "view"}
                  </button>
                </Td>
                <Td className="font-medium">{r.strategy}</Td>
                <Td>
                  <Badge tone="accent">{r.mode}</Badge>
                </Td>
                <Td>{r.status}</Td>
                <Td className="text-muted">{time(r.started_at)}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>

      {m && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Card>
            <div className="text-xs uppercase text-muted">Return</div>
            <div className={`num text-2xl font-semibold ${pnlClass(m.ret)}`}>{num(m.ret)}%</div>
          </Card>
          <Card>
            <div className="text-xs uppercase text-muted">Max drawdown</div>
            <div className="num text-2xl font-semibold text-down">{num(m.maxDd)}%</div>
          </Card>
          <Card>
            <div className="text-xs uppercase text-muted">Win rate</div>
            <div className="num text-2xl font-semibold">{num(m.winRate)}%</div>
          </Card>
          <Card>
            <div className="text-xs uppercase text-muted">Closed trades</div>
            <div className="num text-2xl font-semibold">{m.trades}</div>
          </Card>
        </div>
      )}

      <Card>
        <CardTitle>Equity</CardTitle>
        {points.length ? <EquityChart data={points} /> : <Empty>Select a run with data</Empty>}
      </Card>

      <Card>
        <CardTitle>Trades</CardTitle>
        {!fills.data?.length ? (
          <Empty>No trades</Empty>
        ) : (
          <Table head={["Time", "Symbol", "Side", "Qty", "Price", "PnL"]}>
            {fills.data.slice(0, 100).map((f: any, i: number) => (
              <Tr key={i}>
                <Td className="text-muted">{time(f.ts)}</Td>
                <Td>{f.symbol}</Td>
                <Td>{f.side}</Td>
                <Td className="num">{num(f.qty, 4)}</Td>
                <Td className="num">{usd(f.price)}</Td>
                <Td className={`num ${pnlClass(f.realized_pnl)}`}>{usd(f.realized_pnl)}</Td>
              </Tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
