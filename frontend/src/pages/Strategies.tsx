// Strategy launcher with auto-generated parameter form (REQ-005/006), a panel of
// running/past jobs (view params, stop, reload) and reusable config presets.
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { endpoints, type RunConfig, type RunRow } from "@/lib/api";
import SchemaForm, { defaultsFromSchema, type FormValue } from "@/components/SchemaForm";
import IntervalSelector, { type Interval } from "@/components/IntervalSelector";
import { Badge, Button, Card, CardTitle, Empty, Field, Input, Select } from "@/components/ui";
import { time } from "@/lib/format";

const MODES = ["dry_run", "live", "backtest"];

const MODE_HINT: Record<string, string> = {
  dry_run: "Simulated fills at the live price — no real orders are sent.",
  live: "Places real orders on Bitunix using the selected API key.",
  backtest: "Replays historical candles; finishes on its own.",
};

interface RiskForm {
  min_investment_usd: string;
  max_capital_usd: string;
  max_loss_usd: string;
  base_leverage: string;
  max_leverage: string;
  leverage_step: string;
  allow_hedge: boolean;
}

// Defaults match the recommended preset for a small, capped account
// (~50 USD capital, 5 USDT committed margin per step, 20x), which is what the
// guarded_ladder strategy is tuned for.
const DEFAULT_RISK: RiskForm = {
  min_investment_usd: "5",
  max_capital_usd: "50",
  max_loss_usd: "30",
  base_leverage: "20",
  max_leverage: "50",
  leverage_step: "1",
  allow_hedge: true,
};

// Default symbols: the exact basket guarded_ladder was validated/backtested on
// (top-volume majors + liquid mid-caps), so live/backtest match that validation.
// Clear this field to fall back to auto-selection by 24h volume.
const DEFAULT_SYMBOLS =
  "BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT, DOGEUSDT, AAVEUSDT, TONUSDT, WLDUSDT, LINKUSDT";

// Per-strategy launch presets (risk, interval, starting balance). Selecting a
// strategy loads its preset so the form matches the configuration it was
// validated with. Strategies without an entry here fall back to BASE_PRESET —
// so e.g. guarded_ladder keeps the existing 50 USDT / 20x / 15m defaults.
interface StrategyPreset {
  risk: RiskForm;
  interval: Interval;
  capital: string;
}

const BASE_PRESET: StrategyPreset = {
  risk: DEFAULT_RISK,
  interval: "15m",
  capital: "50",
};

// scalp_momentum: the exact configuration its real-data backtest used
// (10-coin basket on 5m candles, 1000 USDT sim balance, 5 USDT margin per entry,
// 10x leverage). These reproduce the ~+3.3% / ~70% win-rate backtest result.
const STRATEGY_PRESETS: Record<string, StrategyPreset> = {
  scalp_momentum: {
    risk: {
      min_investment_usd: "5",
      max_capital_usd: "100",
      max_loss_usd: "1000",
      base_leverage: "10",
      max_leverage: "20",
      leverage_step: "1",
      allow_hedge: false,
    },
    interval: "5m",
    capital: "1000",
  },
};

function presetFor(name: string): StrategyPreset {
  return STRATEGY_PRESETS[name] ?? BASE_PRESET;
}

const ACTIVE_STATUSES = new Set(["starting", "started", "running"]);

function riskFromConfig(r: Record<string, unknown> | undefined): RiskForm {
  const g = (k: keyof RiskForm, d: string) => String((r?.[k] as unknown) ?? d);
  return {
    min_investment_usd: g("min_investment_usd", DEFAULT_RISK.min_investment_usd),
    max_capital_usd: g("max_capital_usd", DEFAULT_RISK.max_capital_usd),
    max_loss_usd: g("max_loss_usd", DEFAULT_RISK.max_loss_usd),
    base_leverage: g("base_leverage", DEFAULT_RISK.base_leverage),
    max_leverage: g("max_leverage", DEFAULT_RISK.max_leverage),
    leverage_step: g("leverage_step", DEFAULT_RISK.leverage_step),
    allow_hedge: Boolean(r?.allow_hedge ?? true),
  };
}

function statusTone(status: string): "up" | "warn" | "down" | "muted" | "accent" {
  if (ACTIVE_STATUSES.has(status)) return "up";
  if (status === "failed") return "down";
  if (status === "stopped") return "warn";
  return "muted";
}

export default function Strategies() {
  const qc = useQueryClient();
  const { data: schemas } = useQuery({ queryKey: ["strategies"], queryFn: endpoints.strategies });
  const { data: apiKeys } = useQuery({ queryKey: ["apiKeys"], queryFn: endpoints.apiKeys });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: endpoints.runs,
    refetchInterval: 4000,
  });
  const presets = useQuery({
    queryKey: ["strategyConfigs"],
    queryFn: endpoints.strategyConfigs,
  });

  const names = useMemo(() => Object.keys(schemas ?? {}), [schemas]);
  const [strategy, setStrategy] = useState<string>("");
  const [params, setParams] = useState<FormValue>({});
  const [mode, setMode] = useState("dry_run");
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  // Candle interval the strategy trades on. 15m is the recommended/validated
  // default — 1m whipsaws breakout strategies badly.
  const [interval, setInterval] = useState<Interval>("15m");
  const [capital, setCapital] = useState("50");
  const [apiKeyId, setApiKeyId] = useState<string>("");
  const [risk, setRisk] = useState<RiskForm>(DEFAULT_RISK);
  const [result, setResult] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [presetName, setPresetName] = useState("");

  // Load a strategy's parameter defaults plus its launch preset (risk, interval,
  // starting balance). Strategies without a preset get BASE_PRESET.
  const applyStrategy = (name: string) => {
    setStrategy(name);
    setParams(defaultsFromSchema(schemas![name]));
    const preset = presetFor(name);
    setRisk(preset.risk);
    setInterval(preset.interval);
    setCapital(preset.capital);
  };

  // Pick the recommended strategy (trend_scanner) + its defaults when schemas load.
  if (!strategy && names.length) {
    const first = names.includes("trend_scanner") ? "trend_scanner" : names[0];
    applyStrategy(first);
  }

  const onSelectStrategy = (name: string) => {
    applyStrategy(name);
  };

  async function launch() {
    setResult("");
    try {
      const body = {
        mode,
        strategy,
        params,
        symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
        interval,
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
      qc.invalidateQueries({ queryKey: ["runs"] });
    } catch (e) {
      setResult(`Error: ${String(e)}`);
    }
  }

  // Reload a previous run's full config back into the launcher form.
  function loadFromRun(run: RunRow) {
    const cfg: RunConfig = run.config ?? {};
    if (cfg.strategy && schemas?.[cfg.strategy]) {
      setStrategy(cfg.strategy);
      setParams({ ...defaultsFromSchema(schemas[cfg.strategy]), ...(cfg.params ?? {}) });
    } else if (cfg.params) {
      setParams(cfg.params as FormValue);
    }
    if (cfg.mode) setMode(String(cfg.mode));
    if (cfg.interval) setInterval(cfg.interval as Interval);
    setSymbols((cfg.symbols ?? []).join(", "));
    if (cfg.initial_capital != null) setCapital(String(cfg.initial_capital));
    setApiKeyId(cfg.api_key_id != null ? String(cfg.api_key_id) : "");
    setRisk(riskFromConfig(cfg.risk));
    setResult(`Loaded config from run ${run.id.slice(0, 8)}. Review and launch.`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const stop = useMutation({
    mutationFn: (id: string) => endpoints.stopRun(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });

  const savePreset = useMutation({
    mutationFn: () =>
      endpoints.createStrategyConfig({
        name: presetName.trim() || `${strategy} preset`,
        strategy,
        params: params as Record<string, unknown>,
      }),
    onSuccess: () => {
      setPresetName("");
      qc.invalidateQueries({ queryKey: ["strategyConfigs"] });
    },
  });
  const deletePreset = useMutation({
    mutationFn: (id: number) => endpoints.deleteStrategyConfig(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategyConfigs"] }),
  });

  const showCapital = mode !== "live";
  const runList = runs.data ?? [];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardTitle right={<Badge tone="accent">{names.length} available</Badge>}>
            Configure & launch
          </CardTitle>
          <div className="space-y-4">
            <div className={`grid gap-3 ${showCapital ? "grid-cols-3" : "grid-cols-2"}`}>
              <Field
                label="Strategy"
                hint="Which trading logic to run. Each strategy exposes its own parameters below."
              >
                <Select value={strategy} onChange={(e) => onSelectStrategy(e.target.value)}>
                  {names.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Mode" hint={MODE_HINT[mode]}>
                <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                  {MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </Select>
              </Field>
              {showCapital && (
                <Field
                  label="Starting balance (simulation)"
                  hint="Simulated starting balance for dry-run/backtest only. Live uses your real Bitunix balance."
                >
                  <Input type="number" value={capital} onChange={(e) => setCapital(e.target.value)} />
                </Field>
              )}
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto]">
              <Field
                label="Symbols (comma separated)"
                hint="Coins to trade. Leave empty to let the strategy auto-select by 24h volume."
              >
                <Input
                  placeholder="BTCUSDT, ETHUSDT"
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                />
              </Field>
              <Field
                label="Candle interval"
                hint="Timeframe the strategy trades on. 15m (or 5m) is recommended; 1m whipsaws breakout strategies."
              >
                <IntervalSelector value={interval} onChange={setInterval} />
              </Field>
            </div>

            {mode === "live" && (
              <Field
                label="API key (live only)"
                hint="Bitunix credentials used to place real orders. Manage keys in Settings."
              >
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

            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" onClick={launch} disabled={!strategy}>
                {mode === "backtest" ? "Run backtest" : mode === "live" ? "Go LIVE" : "Start dry-run"}
              </Button>
              <div className="flex items-center gap-2">
                <Input
                  placeholder="preset name"
                  value={presetName}
                  onChange={(e) => setPresetName(e.target.value)}
                  className="w-40"
                />
                <Button onClick={() => savePreset.mutate()} disabled={!strategy}>
                  Save preset
                </Button>
              </div>
              {result && <span className="text-sm text-muted">{result}</span>}
            </div>
          </div>
        </Card>

        <Card>
          <CardTitle>Risk & capital</CardTitle>
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Min investment (USD)"
              hint="Committed margin per ladder step; constant regardless of the multiplier."
            >
              <Input
                type="number"
                value={risk.min_investment_usd}
                onChange={(e) => setRisk({ ...risk, min_investment_usd: e.target.value })}
              />
            </Field>
            <Field
              label="Max capital (USD)"
              hint="Hard cap on total margin deployed across open positions."
            >
              <Input
                type="number"
                value={risk.max_capital_usd}
                onChange={(e) => setRisk({ ...risk, max_capital_usd: e.target.value })}
              />
            </Field>
            <Field
              label="Max loss (USD)"
              hint="Stop opening new positions once realized loss reaches this amount."
            >
              <Input
                type="number"
                value={risk.max_loss_usd}
                onChange={(e) => setRisk({ ...risk, max_loss_usd: e.target.value })}
              />
            </Field>
            <Field label="Base leverage (x)" hint="Leverage used for normal entries.">
              <Input
                type="number"
                value={risk.base_leverage}
                onChange={(e) => setRisk({ ...risk, base_leverage: e.target.value })}
              />
            </Field>
            <Field
              label="Max leverage (x)"
              hint="Upper bound the sizer escalates to when an order is below the exchange minimum."
            >
              <Input
                type="number"
                value={risk.max_leverage}
                onChange={(e) => setRisk({ ...risk, max_leverage: e.target.value })}
              />
            </Field>
            <Field label="Leverage step" hint="How much leverage increases per escalation step.">
              <Input
                type="number"
                value={risk.leverage_step}
                onChange={(e) => setRisk({ ...risk, leverage_step: e.target.value })}
              />
            </Field>
            <Field
              label="Allow hedge"
              hint="Permit simultaneous long and short positions on the same symbol."
            >
              <Select
                value={String(risk.allow_hedge)}
                onChange={(e) => setRisk({ ...risk, allow_hedge: e.target.value === "true" })}
              >
                <option value="true">true</option>
                <option value="false">false</option>
              </Select>
            </Field>
          </div>

          {presets.data?.length ? (
            <div className="mt-4">
              <div className="mb-2 text-xs uppercase text-muted">Saved presets</div>
              <div className="space-y-1">
                {presets.data.map((p) => (
                  <div key={p.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">
                      <span className="font-medium">{p.name}</span>{" "}
                      <span className="text-muted">({p.strategy})</span>
                    </span>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        onClick={() => {
                          if (schemas?.[p.strategy]) {
                            setStrategy(p.strategy);
                            setParams({
                              ...defaultsFromSchema(schemas[p.strategy]),
                              ...(p.params ?? {}),
                            });
                            setResult(`Loaded preset "${p.name}".`);
                          }
                        }}
                      >
                        Load
                      </Button>
                      <Button variant="ghost" onClick={() => deletePreset.mutate(p.id)}>
                        Delete
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </Card>
      </div>

      {/* Jobs: running + past strategy runs with their launch params. */}
      <Card>
        <CardTitle
          right={
            <Badge tone="accent">
              {runList.filter((r) => ACTIVE_STATUSES.has(r.status)).length} active
            </Badge>
          }
        >
          Jobs (running & recent strategies)
        </CardTitle>
        {!runList.length ? (
          <Empty>No jobs yet. Launch a strategy above and it will appear here.</Empty>
        ) : (
          <div className="divide-y divide-border/60">
            {runList.map((run) => {
              const cfg = run.config ?? {};
              const active = ACTIVE_STATUSES.has(run.status);
              const open = expanded === run.id;
              const syms = cfg.symbols ?? [];
              return (
                <div key={run.id} className="py-2">
                  <div className="flex flex-wrap items-center gap-3">
                    <Badge tone={statusTone(run.status)}>{run.status}</Badge>
                    <span className="font-medium">{run.strategy}</span>
                    <Badge tone="muted">{run.mode}</Badge>
                    <span className="text-xs text-muted">
                      {syms.length ? `${syms.length} symbol(s)` : "auto-select"} ·{" "}
                      {time(run.started_at)}
                    </span>
                    <div className="ml-auto flex items-center gap-1">
                      <Button variant="ghost" onClick={() => setExpanded(open ? null : run.id)}>
                        {open ? "Hide" : "Details"}
                      </Button>
                      <Button variant="ghost" onClick={() => loadFromRun(run)}>
                        Load into form
                      </Button>
                      {active && (
                        <Button variant="danger" onClick={() => stop.mutate(run.id)}>
                          Stop
                        </Button>
                      )}
                    </div>
                  </div>
                  {open && (
                    <div className="mt-2 grid grid-cols-1 gap-3 lg:grid-cols-2">
                      <div className="text-xs text-muted">
                        <div className="mb-1 uppercase">Run</div>
                        <div>id: {run.id}</div>
                        <div>interval: {String(cfg.interval ?? "—")}</div>
                        <div>
                          starting balance:{" "}
                          {cfg.initial_capital != null ? String(cfg.initial_capital) : "—"}
                        </div>
                        <div>symbols: {syms.length ? syms.join(", ") : "auto-select"}</div>
                      </div>
                      <div>
                        <div className="mb-1 text-xs uppercase text-muted">Parameters</div>
                        <pre className="max-h-48 overflow-auto rounded-lg border border-border bg-panel2 p-2 text-[11px] leading-relaxed text-muted">
                          {JSON.stringify(
                            { params: cfg.params ?? {}, risk: cfg.risk ?? {} },
                            null,
                            2,
                          )}
                        </pre>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
