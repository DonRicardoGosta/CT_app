// System health: connection statuses and live throughput indicators.
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { useRealtime } from "@/store/realtime";
import { Badge, Card, Stat } from "@/components/ui";

export default function Health() {
  const status = useRealtime((s) => s.status);
  const { fills, orders, signals, errors, equityCurve } = useRealtime();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: endpoints.health,
    refetchInterval: 5000,
  });
  const system = useQuery({
    queryKey: ["system-status"],
    queryFn: endpoints.systemStatus,
    refetchInterval: 8000,
  });

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
        <Stat label="Equity points (live)" value={equityCurve.length} />
        <Stat label="Live errors" value={errors.length} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Orders (live buffer)" value={orders.length} />
        <Stat label="Fills (live buffer)" value={fills.length} />
        <Stat label="Signals (live buffer)" value={signals.length} />
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Control bus</span>
          <Badge tone={system.data?.control_bus ? "up" : "down"}>
            {system.data?.control_bus ? "ok" : "down"}
          </Badge>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Realtime hub</span>
          <Badge tone={system.data?.realtime_hub ? "up" : "warn"}>
            {system.data?.realtime_hub ? "ok" : "down"}
          </Badge>
        </Card>
      </div>

      <Card>
        <span className="text-xs uppercase text-muted">Pipeline</span>
        <p className="mt-2 text-sm text-muted">
          API publishes start/stop to Kafka → <strong>trading-worker</strong> runs the engine →
          events → <strong>db-writer</strong> → PostgreSQL. The browser also receives live events
          when the realtime hub is connected to Kafka.
        </p>
        <p className="mt-2 text-sm text-warn">
          If backtests show no data, run:{" "}
          <code className="text-xs">docker compose ps trading-worker db-writer</code>
        </p>
        {system.data?.recent_runs?.length ? (
          <ul className="mt-3 space-y-1 text-sm">
            {system.data.recent_runs.slice(0, 5).map((r) => (
              <li key={r.id} className="font-mono text-xs text-muted">
                {r.id.slice(0, 8)}… {r.mode} — {r.status}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted">No runs in database yet.</p>
        )}
      </Card>
    </div>
  );
}
