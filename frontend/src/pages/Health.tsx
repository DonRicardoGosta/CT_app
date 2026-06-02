// System health: connection status, live throughput, and CPU/RAM history.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, endpoints, type MetricSeries } from "@/lib/api";
import { useRealtime } from "@/store/realtime";
import MetricsChart, { type Series } from "@/components/MetricsChart";
import { Badge, Card, CardTitle, Empty, Stat } from "@/components/ui";

type RangeKey = "15m" | "1h" | "6h" | "24h";
const RANGES: RangeKey[] = ["15m", "1h", "6h", "24h"];

const SERVICE_COLORS: Record<string, string> = {
  "backend-api": "#3b82f6",
  "trading-worker": "#16c784",
  "db-writer": "#f59e0b",
};
function colorFor(service: string): string {
  return SERVICE_COLORS[service] ?? "#9aa7b8";
}

function toCpuSeries(data: MetricSeries[]): Series[] {
  return data.map((s) => ({
    name: s.service,
    color: colorFor(s.service),
    points: s.points.map((p) => ({
      time: Math.floor(new Date(p.ts).getTime() / 1000),
      value: p.cpu_pct,
    })),
  }));
}
function toMemSeries(data: MetricSeries[]): Series[] {
  return data.map((s) => ({
    name: s.service,
    color: colorFor(s.service),
    points: s.points.map((p) => ({
      time: Math.floor(new Date(p.ts).getTime() / 1000),
      value: p.mem_mb,
    })),
  }));
}

export default function Health() {
  const status = useRealtime((s) => s.status);
  const { fills, orders, signals, errors } = useRealtime();
  const [range, setRange] = useState<RangeKey>("1h");

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<{ status: string }>("/../health").catch(() => ({ status: "unknown" })),
    refetchInterval: 5000,
  });

  const metrics = useQuery({
    queryKey: ["system-metrics", range],
    queryFn: () => endpoints.systemMetrics(range),
    refetchInterval: 5000,
  });
  const snapshot = useQuery({
    queryKey: ["system-status"],
    queryFn: () => endpoints.systemStatus(),
    refetchInterval: 5000,
  });

  const cpuSeries = useMemo(() => toCpuSeries(metrics.data ?? []), [metrics.data]);
  const memSeries = useMemo(() => toMemSeries(metrics.data ?? []), [metrics.data]);
  const hasMetrics = (metrics.data ?? []).some((s) => s.points.length > 0);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">API</span>
          <Badge tone={health.data?.status === "ok" ? "up" : "warn"}>
            {health.data?.status ?? "…"}
          </Badge>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Realtime WS</span>
          <Badge tone={status === "open" ? "up" : status === "connecting" ? "warn" : "down"}>
            {status}
          </Badge>
        </Card>
        <Stat label="Orders (live buffer)" value={orders.length} />
        <Stat label="Errors (live buffer)" value={errors.length} />
      </div>

      {/* Current CPU/RAM per service */}
      <Card>
        <CardTitle right={<Badge tone="muted">now</Badge>}>Resource usage by service</CardTitle>
        {snapshot.data && snapshot.data.length ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {snapshot.data.map((s) => (
              <div key={s.service} className="rounded-lg border border-border bg-panel2 p-3">
                <div className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: colorFor(s.service) }}
                  />
                  <span className="text-sm font-medium">{s.service}</span>
                </div>
                <div className="mt-2 flex items-baseline justify-between text-sm">
                  <span className="text-muted">CPU</span>
                  <span className="num">{s.cpu_pct.toFixed(1)}%</span>
                </div>
                <div className="flex items-baseline justify-between text-sm">
                  <span className="text-muted">RAM</span>
                  <span className="num">
                    {s.mem_mb.toFixed(0)} MB
                    {s.mem_limit_mb ? ` / ${s.mem_limit_mb.toFixed(0)}` : ""}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No samples yet — the metrics sampler records every ~5s.</Empty>
        )}
      </Card>

      {/* History */}
      <Card>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <CardTitle>CPU &amp; RAM history</CardTitle>
          <div className="flex items-center gap-1 rounded-lg border border-border bg-panel2 p-0.5">
            {RANGES.map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`rounded-md px-2 py-1 text-xs ${
                  range === r ? "bg-accent text-white" : "text-muted hover:text-text"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
        {hasMetrics ? (
          <div className="space-y-4">
            <div>
              <div className="mb-1 text-xs uppercase text-muted">CPU %</div>
              <MetricsChart series={cpuSeries} suffix="%" />
            </div>
            <div>
              <div className="mb-1 text-xs uppercase text-muted">Memory (MB)</div>
              <MetricsChart series={memSeries} />
            </div>
            <div className="flex flex-wrap gap-4 text-xs text-muted">
              {(metrics.data ?? []).map((s) => (
                <span key={s.service} className="flex items-center gap-1.5">
                  <span
                    className="inline-block h-2 w-3 align-middle"
                    style={{ background: colorFor(s.service) }}
                  />
                  {s.service}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <Empty>No metrics in this range yet.</Empty>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Fills (live buffer)" value={fills.length} />
        <Stat label="Signals (live buffer)" value={signals.length} />
        <Card className="flex flex-col gap-1 lg:col-span-2">
          <span className="text-xs uppercase text-muted">Pipeline</span>
          <span className="text-sm text-muted">
            Engine → Kafka → db_writer → PostgreSQL. The hot path is DB-free; events
            buffer in Kafka if the database is slow.
          </span>
        </Card>
      </div>
    </div>
  );
}
