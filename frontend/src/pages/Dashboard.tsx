// Realtime overview (WS-driven, no DB), fully separated by mode (Live vs Dry).
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useMode, type ModeBucket, type TradeMode } from "@/store/realtime";
import { endpoints } from "@/lib/api";
import { Card, CardTitle, Empty, Stat, Table, Td, Tr } from "@/components/ui";
import EquityChart from "@/components/EquityChart";
import { pnlClass, time, usd } from "@/lib/format";

function useLiveBalance(enabled: boolean): string | null {
  const keys = useQuery({
    queryKey: ["api-keys"],
    queryFn: () => endpoints.apiKeys(),
    enabled,
    retry: false,
  });
  const activeId = keys.data?.find((k) => k.is_active)?.id ?? keys.data?.[0]?.id;
  const bal = useQuery({
    queryKey: ["account-balance", activeId],
    queryFn: () => endpoints.accountBalance(activeId),
    enabled: enabled && activeId != null,
    refetchInterval: 15000,
    retry: false,
  });
  return bal.data?.balance ?? null;
}

function ModeSection({ mode, bucket }: { mode: TradeMode; bucket: ModeBucket }) {
  const posList = Object.values(bucket.positions);
  const uPnl = posList.reduce((a, p) => a + parseFloat(p.unrealized_pnl || "0"), 0);
  const points = useMemo(
    () => bucket.equityCurve.map((p) => ({ time: Math.floor(p.ts / 1000), value: p.equity })),
    [bucket.equityCurve],
  );

  // Live balance comes from the exchange account; dry from the run's equity event.
  const liveBalance = useLiveBalance(mode === "live");
  const equityBalance = bucket.equity ? String((bucket.equity as any).balance) : null;
  const balance = mode === "live" ? (liveBalance ?? equityBalance) : equityBalance;
  const balanceSub = mode === "live" ? "Bitunix account" : "simulated (run)";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Equity" value={bucket.equity ? usd(bucket.equity.equity) : "—"} />
        <Stat label="Balance" value={balance != null ? usd(balance) : "—"} sub={balanceSub} />
        <Stat label="Unrealized PnL" value={usd(uPnl)} className={pnlClass(uPnl)} />
        <Stat label="Open positions" value={posList.length} />
      </div>

      <Card>
        <CardTitle>Equity ({mode === "live" ? "live" : "dry-run"})</CardTitle>
        {points.length ? <EquityChart data={points} /> : <Empty>Waiting for equity…</Empty>}
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardTitle>Recent fills</CardTitle>
          {bucket.fills.length === 0 ? (
            <Empty>No fills yet</Empty>
          ) : (
            <Table head={["Time", "Symbol", "Side", "Qty", "Price", "PnL"]}>
              {bucket.fills.slice(0, 12).map((f, i) => (
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
          {bucket.errors.length === 0 ? (
            <Empty>No errors</Empty>
          ) : (
            <Table head={["Time", "Source", "Severity", "Message"]}>
              {bucket.errors.slice(0, 12).map((e, i) => (
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

export default function Dashboard() {
  const [mode, setMode] = useState<TradeMode>("live");
  const live = useMode("live");
  const dry = useMode("dry_run");
  const bucket = mode === "live" ? live : dry;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-1 rounded-lg border border-border bg-panel2 p-0.5 w-fit">
        {(["live", "dry_run"] as TradeMode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`rounded-md px-4 py-1.5 text-sm font-medium transition-colors ${
              mode === m ? "bg-accent text-white" : "text-muted hover:text-text"
            }`}
          >
            {m === "live" ? "Live" : "Dry-run"}
          </button>
        ))}
      </div>

      <ModeSection mode={mode} bucket={bucket} />
    </div>
  );
}
