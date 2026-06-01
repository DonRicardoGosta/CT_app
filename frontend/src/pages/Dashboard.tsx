// Realtime overview (WS-driven, no DB).
import { useRealtime } from "@/store/realtime";
import { Card, CardTitle, Empty, Stat, Table, Td, Tr } from "@/components/ui";
import EquityChart from "@/components/EquityChart";
import { pnlClass, time, usd } from "@/lib/format";

export default function Dashboard() {
  const { equity, equityCurve, positions, fills, errors } = useRealtime();
  const posList = Object.values(positions);
  const uPnl = posList.reduce((a, p) => a + parseFloat(p.unrealized_pnl || "0"), 0);
  const points = equityCurve.map((p) => ({ time: Math.floor(p.ts / 1000), value: p.equity }));

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Equity" value={equity ? usd(equity.equity) : "—"} />
        <Stat label="Balance" value={equity ? usd((equity as any).balance) : "—"} />
        <Stat
          label="Unrealized PnL"
          value={usd(uPnl)}
          className={pnlClass(uPnl)}
        />
        <Stat label="Open positions" value={posList.length} />
      </div>

      <Card>
        <CardTitle>Equity (live)</CardTitle>
        {points.length ? <EquityChart data={points} /> : <Empty>Waiting for live equity…</Empty>}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>Recent fills</CardTitle>
          {fills.length === 0 ? (
            <Empty>No fills yet</Empty>
          ) : (
            <Table head={["Time", "Symbol", "Side", "Qty", "Price", "PnL"]}>
              {fills.slice(0, 12).map((f, i) => (
                <Tr key={i}>
                  <Td className="text-muted">{time(f.ts)}</Td>
                  <Td>{String(f.symbol)}</Td>
                  <Td>{String(f.side)}</Td>
                  <Td className="num">{String(f.qty)}</Td>
                  <Td className="num">{usd(f.price)}</Td>
                  <Td className={`num ${pnlClass(f.realized_pnl)}`}>{usd(f.realized_pnl)}</Td>
                </Tr>
              ))}
            </Table>
          )}
        </Card>

        <Card>
          <CardTitle>Recent errors</CardTitle>
          {errors.length === 0 ? (
            <Empty>No errors</Empty>
          ) : (
            <Table head={["Time", "Source", "Severity", "Message"]}>
              {errors.slice(0, 12).map((e, i) => (
                <Tr key={i}>
                  <Td className="text-muted">{time(e.ts)}</Td>
                  <Td>{String(e.source)}</Td>
                  <Td>{String(e.severity)}</Td>
                  <Td className="max-w-xs truncate">{String(e.message)}</Td>
                </Tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}
