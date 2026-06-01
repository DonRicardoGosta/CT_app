// Backtest launcher + results (DB-backed results after the run completes).
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import SchemaForm, { defaultsFromSchema, type FormValue } from "@/components/SchemaForm";
import EquityChart from "@/components/EquityChart";
import { Badge, Button, Card, CardTitle, Empty, Field, Input, Select, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

export default function Backtest() {
  const { data: schemas } = useQuery({ queryKey: ["strategies"], queryFn: endpoints.strategies });
  const names = useMemo(() => Object.keys(schemas ?? {}), [schemas]);

  const [strategy, setStrategy] = useState("");
  const [params, setParams] = useState<FormValue>({});
  const [symbols, setSymbols] = useState("BTCUSDT");
  const [interval, setInterval] = useState("1m");
  const [capital, setCapital] = useState("1000");
  const [limit, setLimit] = useState("1000");
  const [runId, setRunId] = useState<string>("");
  const [msg, setMsg] = useState("");

  if (!strategy && names.length) {
    setStrategy(names[0]);
    setParams(defaultsFromSchema(schemas![names[0]]));
  }

  const equity = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => endpoints.equity(runId),
    enabled: !!runId,
    refetchInterval: 3000,
  });
  const fills = useQuery({
    queryKey: ["fills", runId],
    queryFn: () => endpoints.fills(runId),
    enabled: !!runId,
    refetchInterval: 3000,
  });

  async function run() {
    setMsg("");
    try {
      const res = await endpoints.startRun({
        mode: "backtest",
        strategy,
        params,
        symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
        interval,
        initial_capital: capital,
        backtest_limit: Number(limit),
        risk: { min_investment_usd: "1", max_capital_usd: "100", base_leverage: 5, max_leverage: 20 },
      });
      setRunId(res.run_id);
      setMsg(`Backtest ${res.run_id} started — results stream in below.`);
    } catch (e) {
      setMsg(`Error: ${String(e)}`);
    }
  }

  const points = (equity.data ?? []).map((e: any) => ({
    time: Math.floor(new Date(e.ts).getTime() / 1000),
    value: parseFloat(e.equity),
  }));
  const last = equity.data?.[equity.data.length - 1];

  return (
    <div className="space-y-5">
      <Card>
        <CardTitle right={<Badge tone="accent">backtest</Badge>}>Configure backtest</CardTitle>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Field label="Strategy">
            <Select
              value={strategy}
              onChange={(e) => {
                setStrategy(e.target.value);
                setParams(defaultsFromSchema(schemas![e.target.value]));
              }}
            >
              {names.map((n) => (
                <option key={n}>{n}</option>
              ))}
            </Select>
          </Field>
          <Field label="Symbols">
            <Input value={symbols} onChange={(e) => setSymbols(e.target.value)} />
          </Field>
          <Field label="Interval">
            <Select value={interval} onChange={(e) => setInterval(e.target.value)}>
              {["1m", "5m", "15m", "1h"].map((i) => (
                <option key={i}>{i}</option>
              ))}
            </Select>
          </Field>
          <Field label="Initial capital">
            <Input type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
          </Field>
          <Field label="Bars (limit)">
            <Input type="number" value={limit} onChange={(e) => setLimit(e.target.value)} />
          </Field>
        </div>
        <div className="mt-3">
          {strategy && schemas?.[strategy] && (
            <SchemaForm schema={schemas[strategy]} value={params} onChange={setParams} />
          )}
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" onClick={run} disabled={!strategy}>
            Run backtest
          </Button>
          {msg && <span className="text-sm text-muted">{msg}</span>}
        </div>
      </Card>

      {runId && (
        <>
          <Card>
            <CardTitle right={last ? <Badge tone="accent">final {usd(last.equity)}</Badge> : null}>
              Equity curve
            </CardTitle>
            {points.length ? <EquityChart data={points} /> : <Empty>Waiting for results…</Empty>}
          </Card>
          <Card>
            <CardTitle>Trades</CardTitle>
            {!fills.data?.length ? (
              <Empty>No trades yet</Empty>
            ) : (
              <Table head={["Time", "Symbol", "Side", "Qty", "Price", "PnL"]}>
                {fills.data.slice(0, 50).map((f: any, i: number) => (
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
        </>
      )}
    </div>
  );
}
