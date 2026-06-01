// System health: connection statuses and live throughput indicators.
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useRealtime } from "@/store/realtime";
import { Badge, Card, Stat } from "@/components/ui";

export default function Health() {
  const status = useRealtime((s) => s.status);
  const { fills, orders, signals, errors, equityCurve } = useRealtime();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<{ status: string }>("/../health").catch(() => ({ status: "unknown" })),
    refetchInterval: 5000,
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
