// Grafana/Loki-like log explorer: persisted logs + live WS events.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints, type LogRow } from "@/lib/api";
import { useRealtime } from "@/store/realtime";
import { Badge, Button, Card, Empty, Input, Select } from "@/components/ui";
import { time } from "@/lib/format";

type RangeKey = "5m" | "15m" | "1h" | "24h" | "all";

const RANGES: { key: RangeKey; label: string; ms: number | null }[] = [
  { key: "5m", label: "Last 5m", ms: 5 * 60_000 },
  { key: "15m", label: "15m", ms: 15 * 60_000 },
  { key: "1h", label: "1h", ms: 60 * 60_000 },
  { key: "24h", label: "24h", ms: 24 * 60 * 60_000 },
  { key: "all", label: "All", ms: null },
];

const SOURCES = ["all", "engine", "run", "signal", "order", "fill", "risk", "builder", "run_manager", "trading_worker"];
const SEVERITIES = ["all", "info", "warn", "error"];

function severityTone(sev: string) {
  if (sev === "error" || sev === "critical") return "down";
  if (sev === "warning" || sev === "warn") return "warn";
  if (sev === "info") return "accent";
  return "muted";
}

function logKey(row: LogRow): string {
  return `${row.kind}|${row.ts}|${row.source}|${row.run_id ?? ""}|${row.symbol ?? ""}|${row.message}`;
}

function liveErrorToLog(e: any): LogRow {
  return {
    ts: String(e.ts ?? new Date().toISOString()),
    severity: String(e.severity ?? "error"),
    source: String(e.source ?? "error"),
    message: String(e.message ?? e.detail ?? "error"),
    run_id: e.run_id ? String(e.run_id) : null,
    mode: e.mode ? String(e.mode) : null,
    symbol: e.symbol ? String(e.symbol) : null,
    kind: "error",
    context: { detail: e.detail, ...(typeof e.context === "object" ? e.context : {}) },
  };
}

function liveRunToLog(e: any): LogRow {
  const status = String(e.status ?? "unknown");
  return {
    ts: String(e.ts ?? new Date().toISOString()),
    severity: status === "failed" ? "error" : "info",
    source: "run",
    message: `run ${status}: ${String(e.strategy ?? "")} (${String(e.mode ?? "")})`,
    run_id: e.run_id ? String(e.run_id) : null,
    mode: e.mode ? String(e.mode) : null,
    kind: "run",
    context: { strategy: e.strategy, status, detail: e.detail },
  };
}

function liveOrderToLog(e: any): LogRow {
  return {
    ts: String(e.ts ?? new Date().toISOString()),
    severity: "info",
    source: "order",
    message: `order ${String(e.status ?? "")}: ${String(e.side ?? "")} ${String(e.symbol ?? "")} qty=${String(e.qty ?? "")}`,
    run_id: e.run_id ? String(e.run_id) : null,
    mode: e.mode ? String(e.mode) : null,
    symbol: e.symbol ? String(e.symbol) : null,
    kind: "order",
    context: e,
  };
}

function liveFillToLog(e: any): LogRow {
  return {
    ts: String(e.ts ?? new Date().toISOString()),
    severity: "info",
    source: "fill",
    message: `fill ${String(e.side ?? "")} ${String(e.symbol ?? "")} qty=${String(e.qty ?? "")} @ ${String(e.price ?? "")}`,
    run_id: e.run_id ? String(e.run_id) : null,
    mode: e.mode ? String(e.mode) : null,
    symbol: e.symbol ? String(e.symbol) : null,
    kind: "fill",
    context: e,
  };
}

function liveSignalToLog(e: any): LogRow {
  return {
    ts: String(e.ts ?? new Date().toISOString()),
    severity: "info",
    source: "signal",
    message: `signal ${String(e.action ?? "")}: ${String(e.side ?? "")} ${String(e.symbol ?? "")} (${String(e.reason ?? "")})`,
    run_id: e.run_id ? String(e.run_id) : null,
    mode: e.mode ? String(e.mode) : null,
    symbol: e.symbol ? String(e.symbol) : null,
    kind: "signal",
    context: e,
  };
}

function passesClientFilters(row: LogRow, range: RangeKey): boolean {
  const cfg = RANGES.find((r) => r.key === range);
  if (!cfg?.ms) return true;
  const ts = new Date(row.ts).getTime();
  return isFinite(ts) && ts >= Date.now() - cfg.ms;
}

export default function Logs() {
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("all");
  const [source, setSource] = useState("all");
  const [range, setRange] = useState<RangeKey>("1h");
  const [liveTail, setLiveTail] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const wsStatus = useRealtime((s) => s.status);
  const { runs, orders, fills, signals, errors } = useRealtime();

  const logsQuery = useQuery({
    queryKey: ["logs", query, severity, source],
    queryFn: () =>
      endpoints.logs({
        q: query || undefined,
        severity: severity === "all" ? undefined : severity,
        source: source === "all" ? undefined : source,
        limit: 1000,
      }),
    refetchInterval: liveTail ? 3000 : false,
  });

  const liveRows = useMemo(() => {
    if (!liveTail) return [];
    return [
      ...errors.map(liveErrorToLog),
      ...runs.map(liveRunToLog),
      ...orders.map(liveOrderToLog),
      ...fills.map(liveFillToLog),
      ...signals.map(liveSignalToLog),
    ];
  }, [liveTail, errors, runs, orders, fills, signals]);

  const rows = useMemo(() => {
    const byKey = new Map<string, LogRow>();
    for (const row of [...liveRows, ...(logsQuery.data ?? [])]) {
      if (severity !== "all" && row.severity !== severity) continue;
      if (source !== "all" && row.source !== source) continue;
      if (query) {
        const blob = `${row.ts} ${row.severity} ${row.source} ${row.message} ${row.run_id ?? ""} ${row.mode ?? ""} ${row.symbol ?? ""} ${JSON.stringify(row.context ?? {})}`.toLowerCase();
        if (!blob.includes(query.toLowerCase())) continue;
      }
      if (!passesClientFilters(row, range)) continue;
      byKey.set(logKey(row), row);
    }
    return [...byKey.values()].sort(
      (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
    );
  }, [liveRows, logsQuery.data, severity, source, query, range]);

  const errorCount = rows.filter((r) => r.severity === "error" || r.severity === "critical").length;
  const warnCount = rows.filter((r) => r.severity === "warn" || r.severity === "warning").length;
  const lastTs = rows[0]?.ts;

  return (
    <div className="space-y-4">
      <Card className="sticky top-0 z-10 border-border/80 bg-panel/95 backdrop-blur">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">Logs & Errors</h2>
            <p className="text-xs text-muted">Grafana-like live tail from WebSocket + persisted history</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={wsStatus === "open" ? "up" : wsStatus === "connecting" ? "warn" : "down"}>
              WS {wsStatus}
            </Badge>
            <Button onClick={() => logsQuery.refetch()} variant="ghost">
              Refresh
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_auto_auto_auto_auto]">
          <Input
            placeholder="Search logs, run id, symbol, context..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full"
          />
          <Select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            {SEVERITIES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Select value={source} onChange={(e) => setSource(e.target.value)}>
            {SOURCES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <div className="flex items-center gap-1 rounded-lg border border-border bg-panel2 p-0.5">
            {RANGES.map((r) => (
              <button
                key={r.key}
                onClick={() => setRange(r.key)}
                className={`rounded-md px-2 py-1 text-xs ${
                  range === r.key ? "bg-accent text-white" : "text-muted hover:text-text"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 rounded-lg border border-border bg-panel2 px-3 py-1.5 text-sm">
            <input
              type="checkbox"
              checked={liveTail}
              onChange={(e) => setLiveTail(e.target.checked)}
            />
            Live tail
          </label>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card className="py-3">
          <div className="text-xs uppercase text-muted">Rows</div>
          <div className="num text-2xl font-semibold">{rows.length}</div>
        </Card>
        <Card className="py-3">
          <div className="text-xs uppercase text-muted">Errors</div>
          <div className="num text-2xl font-semibold text-down">{errorCount}</div>
        </Card>
        <Card className="py-3">
          <div className="text-xs uppercase text-muted">Warnings</div>
          <div className="num text-2xl font-semibold text-warn">{warnCount}</div>
        </Card>
        <Card className="py-3">
          <div className="text-xs uppercase text-muted">Last event</div>
          <div className="text-sm font-medium">{lastTs ? time(lastTs) : "—"}</div>
        </Card>
      </div>

      <Card className="bg-[#0b0f16] p-0">
        {logsQuery.isError && (
          <div className="border-b border-border px-4 py-3 text-sm text-down">
            Could not load persisted logs. Is backend-api / PostgreSQL / db-writer running?
          </div>
        )}
        {!rows.length ? (
          <Empty>
            No log entries. Start a run, enable Live tail, or check that db-writer is running.
          </Empty>
        ) : (
          <div className="max-h-[65vh] overflow-auto font-mono text-xs">
            {rows.map((row) => {
              const key = logKey(row);
              const open = expanded === key;
              return (
                <div key={key} className="border-b border-border/60">
                  <button
                    onClick={() => setExpanded(open ? null : key)}
                    className="grid w-full grid-cols-[190px_70px_120px_120px_1fr] gap-3 px-3 py-2 text-left hover:bg-panel2/60"
                  >
                    <span className="text-muted">{time(row.ts)}</span>
                    <span>
                      <Badge tone={severityTone(row.severity)}>{row.severity}</Badge>
                    </span>
                    <span className="text-accent">{row.source}</span>
                    <span className="truncate text-muted">
                      {row.symbol ?? (row.run_id ? String(row.run_id).slice(0, 8) : "—")}
                    </span>
                    <span className="truncate text-text">{row.message}</span>
                  </button>
                  {open && (
                    <div className="bg-black/30 px-3 pb-3 pt-1">
                      <div className="mb-2 grid grid-cols-2 gap-2 text-xs text-muted lg:grid-cols-4">
                        <div>kind: {row.kind}</div>
                        <div>mode: {row.mode ?? "—"}</div>
                        <div>run: {row.run_id ?? "—"}</div>
                        <div>symbol: {row.symbol ?? "—"}</div>
                      </div>
                      <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-panel p-3 text-[11px] leading-relaxed text-muted">
                        {JSON.stringify(row.context ?? {}, null, 2)}
                      </pre>
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
