// Strategy launcher with auto-generated parameter form (REQ-005/006).
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import SchemaForm, { defaultsFromSchema, type FormValue } from "@/components/SchemaForm";
import StrategyPlanView from "@/components/StrategyPlanView";
import { Badge, Button, Card, CardTitle, Field, Input, Select } from "@/components/ui";

const MODES = ["dry_run", "live", "backtest"];

export default function Strategies() {
  const { data: schemas } = useQuery({ queryKey: ["strategies"], queryFn: endpoints.strategies });
  const { data: apiKeys } = useQuery({ queryKey: ["apiKeys"], queryFn: endpoints.apiKeys });

  const names = useMemo(() => Object.keys(schemas ?? {}), [schemas]);
  const [strategy, setStrategy] = useState<string>("");
  const [params, setParams] = useState<FormValue>({});
  const [mode, setMode] = useState("dry_run");
  const [symbols, setSymbols] = useState("");
  const [capital, setCapital] = useState("1000");
  const [apiKeyId, setApiKeyId] = useState<string>("");
  const [risk, setRisk] = useState({
    min_investment_usd: "1",
    max_capital_usd: "100",
    max_loss_usd: "50",
    base_leverage: "5",
    max_leverage: "20",
    leverage_step: "1",
    allow_hedge: true,
  });
  const [result, setResult] = useState<string>("");

  // Pick the first strategy + its defaults when schemas load.
  if (!strategy && names.length) {
    const first = names[0];
    setStrategy(first);
    setParams(defaultsFromSchema(schemas![first]));
  }

  const onSelectStrategy = (name: string) => {
    setStrategy(name);
    setParams(defaultsFromSchema(schemas![name]));
  };

  async function launch() {
    setResult("");
    try {
      const body = {
        mode,
        strategy,
        params,
        symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
        initial_capital: capital,
        api_key_id: apiKeyId ? Number(apiKeyId) : null,
        risk: {
          ...risk,
          base_leverage: Number(risk.base_leverage),
          max_leverage: Number(risk.max_leverage),
          leverage_step: Number(risk.leverage_step),
        },
      };
      const res = await endpoints.startRun(body);
      setResult(`Started run ${res.run_id} (${res.mode}).`);
    } catch (e) {
      setResult(`Error: ${String(e)}`);
    }
  }

  return (
    <div className="space-y-5">
      <StrategyPlanView />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardTitle right={<Badge tone="accent">{names.length} available</Badge>}>
          Configure & launch
        </CardTitle>
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <Field label="Strategy">
              <Select value={strategy} onChange={(e) => onSelectStrategy(e.target.value)}>
                {names.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Mode">
              <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                {MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Initial capital (USD)">
              <Input type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
            </Field>
          </div>

          <Field label="Symbols (comma separated, empty = auto-select)">
            <Input
              placeholder="BTCUSDT, ETHUSDT"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
            />
          </Field>

          {mode === "live" && (
            <Field label="API key (live only)">
              <Select value={apiKeyId} onChange={(e) => setApiKeyId(e.target.value)}>
                <option value="">— select —</option>
                {(apiKeys ?? []).map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name} ({k.api_key_masked})
                  </option>
                ))}
              </Select>
            </Field>
          )}

          <div>
            <div className="mb-2 text-xs uppercase text-muted">Strategy parameters</div>
            {strategy && schemas?.[strategy] ? (
              <SchemaForm schema={schemas[strategy]} value={params} onChange={setParams} />
            ) : null}
          </div>

          <div className="flex items-center gap-3">
            <Button variant="primary" onClick={launch} disabled={!strategy}>
              {mode === "backtest" ? "Run backtest" : mode === "live" ? "Go LIVE" : "Start dry-run"}
            </Button>
            {result && <span className="text-sm text-muted">{result}</span>}
          </div>
        </div>
      </Card>

      <Card>
        <CardTitle>Risk & capital</CardTitle>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Min investment (USD)">
            <Input
              type="number"
              value={risk.min_investment_usd}
              onChange={(e) => setRisk({ ...risk, min_investment_usd: e.target.value })}
            />
          </Field>
          <Field label="Max capital (USD)">
            <Input
              type="number"
              value={risk.max_capital_usd}
              onChange={(e) => setRisk({ ...risk, max_capital_usd: e.target.value })}
            />
          </Field>
          <Field label="Max loss (USD)">
            <Input
              type="number"
              value={risk.max_loss_usd}
              onChange={(e) => setRisk({ ...risk, max_loss_usd: e.target.value })}
            />
          </Field>
          <Field label="Base leverage (x)">
            <Input
              type="number"
              value={risk.base_leverage}
              onChange={(e) => setRisk({ ...risk, base_leverage: e.target.value })}
            />
          </Field>
          <Field label="Max leverage (x)">
            <Input
              type="number"
              value={risk.max_leverage}
              onChange={(e) => setRisk({ ...risk, max_leverage: e.target.value })}
            />
          </Field>
          <Field label="Leverage step">
            <Input
              type="number"
              value={risk.leverage_step}
              onChange={(e) => setRisk({ ...risk, leverage_step: e.target.value })}
            />
          </Field>
          <Field label="Allow hedge">
            <Select
              value={String(risk.allow_hedge)}
              onChange={(e) => setRisk({ ...risk, allow_hedge: e.target.value === "true" })}
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </Select>
          </Field>
        </div>
        <p className="mt-3 text-xs text-muted">
          Min investment is the committed margin per ladder step and stays constant
          regardless of the multiplier. If an order is below the exchange minimum, the
          multiplier is increased automatically up to the max.
        </p>
      </Card>
    </div>
    </div>
  );
}
