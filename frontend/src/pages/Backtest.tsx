// Backtest launcher + results (date range, leverage, TP/SL on margin).
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import SchemaForm, { defaultsFromSchema, type FormValue } from "@/components/SchemaForm";
import EquityChart from "@/components/EquityChart";
import RunStatusPanel from "@/components/RunStatusPanel";
import { useRunMonitor } from "@/hooks/useRunMonitor";
import { Badge, Button, Card, CardTitle, Empty, Field, Input, Select, Table, Td, Tr } from "@/components/ui";
import { num, pnlClass, time, usd } from "@/lib/format";

function toIsoDate(d: string, endOfDay: boolean): string | undefined {
  if (!d) return undefined;
  const suffix = endOfDay ? "T23:59:59" : "T00:00:00";
  return new Date(`${d}${suffix}`).toISOString();
}

export default function Backtest() {
  const { data: schemas } = useQuery({ queryKey: ["strategies"], queryFn: endpoints.strategies });
  const names = useMemo(() => Object.keys(schemas ?? {}), [schemas]);

  const [strategy, setStrategy] = useState("");
  const [params, setParams] = useState<FormValue>({});
  const [symbols, setSymbols] = useState("");
  const [interval, setInterval] = useState("1m");
  const [capital, setCapital] = useState("1000");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [leverage, setLeverage] = useState("10");
  const [minInvest, setMinInvest] = useState("1");
  const [maxCapital, setMaxCapital] = useState("100");
  const [runId, setRunId] = useState<string>("");
  const [msg, setMsg] = useState("");

  const { status, runEquity } = useRunMonitor(runId || null);

  if (!strategy && names.length) {
    setStrategy(names[0]);
    setParams(defaultsFromSchema(schemas![names[0]]));
  }

  const equity = useQuery({
    queryKey: ["equity", runId],
    queryFn: () => endpoints.equity(runId),
    enabled: !!runId,
    refetchInterval: status === "finished" || status === "failed" ? 4000 : 1500,
  });
  const fills = useQuery({
    queryKey: ["fills", runId],
    queryFn: () => endpoints.fills(runId),
    enabled: !!runId,
    refetchInterval: status === "finished" || status === "failed" ? 4000 : 1500,
  });

  async function run() {
    setMsg("");
    if (!fromDate || !toDate) {
      setMsg("Please set both From date and To date.");
      return;
    }
    try {
      const res = await endpoints.startRun({
        mode: "backtest",
        strategy,
        params,
        symbols: symbols
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        interval,
        initial_capital: capital,
        backtest_start: toIsoDate(fromDate, false),
        backtest_end: toIsoDate(toDate, true),
        risk: {
          min_investment_usd: minInvest,
          max_capital_usd: maxCapital,
          max_loss_usd: maxCapital,
          base_leverage: Number(leverage),
          max_leverage: Number(leverage),
          leverage_step: 1,
        },
      });
      setRunId(res.run_id);
      setMsg(`Backtest started — see status below.`);
    } catch (e) {
      setMsg(`Error: ${String(e)}`);
    }
  }

  const dbPoints = (equity.data ?? []).map((e: any) => ({
    time: Math.floor(new Date(e.ts).getTime() / 1000),
    value: parseFloat(e.equity),
  }));
  const wsPoints = runEquity.map((p) => ({
    time: Math.floor(p.ts / 1000),
    value: p.equity,
  }));
  const points = dbPoints.length >= wsPoints.length ? dbPoints : wsPoints;
  const last = equity.data?.[equity.data.length - 1];
  const waiting =
    !!runId &&
    !points.length &&
    (status === "started" || status === "running" || status === null || status === "unknown");

  return (
    <div className="space-y-5">
      <Card>
        <CardTitle right={<Badge tone="accent">backtest</Badge>}>Configure backtest</CardTitle>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
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
          <Field label="Symbols (empty = auto up to 5)">
            <Input
              placeholder="BTCUSDT, ETHUSDT"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
            />
          </Field>
          <Field label="Interval">
            <Select value={interval} onChange={(e) => setInterval(e.target.value)}>
              {["1m", "5m", "15m", "1h"].map((i) => (
                <option key={i}>{i}</option>
              ))}
            </Select>
          </Field>
          <Field label="Initial capital (USD)">
            <Input type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
          </Field>
          <Field label="From date">
            <Input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </Field>
          <Field label="To date">
            <Input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
          </Field>
          <Field label="Leverage (x)">
            <Input type="number" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
          </Field>
          <Field label="Min investment / step (USD)">
            <Input type="number" value={minInvest} onChange={(e) => setMinInvest(e.target.value)} />
          </Field>
          <Field label="Max capital (USD)">
            <Input type="number" value={maxCapital} onChange={(e) => setMaxCapital(e.target.value)} />
          </Field>
        </div>
        <p className="mt-2 text-xs text-muted">
          TP/SL % in strategy params are on margin (ROE). Price levels use leverage: e.g. 2% SL at 10x ≈
          0.2% price move.
        </p>
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
          <RunStatusPanel runId={runId} />

          <Card>
            <CardTitle
              right={
                last ? (
                  <Badge tone="accent">final {usd(last.equity)}</Badge>
                ) : status ? (
                  <Badge tone={status === "finished" ? "up" : "warn"}>{status}</Badge>
                ) : null
              }
            >
              Equity curve
            </CardTitle>
            {points.length ? (
              <EquityChart data={points} />
            ) : waiting ? (
              <Empty>Running backtest… equity points will appear here (WebSocket + database).</Empty>
            ) : (
              <Empty>
                No equity data. Check errors above — often no klines for the date range or
                trading-worker / db-writer not running.
              </Empty>
            )}
          </Card>

          <Card>
            <CardTitle>Trades</CardTitle>
            {!fills.data?.length ? (
              <Empty>
                {status === "finished" ? "No trades in this backtest" : "Waiting for trades…"}
              </Empty>
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
