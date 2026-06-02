// System health: API/runtime statuses and live throughput indicators.
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { useRealtime } from "@/store/realtime";
import { Badge, Card, Stat } from "@/components/ui";

function tone(value: string | undefined): string {
  if (value === "ok" || value === "ready" || value === "running" || value === "open") return "up";
  if (!value || value === "unknown") return "warn";
  return "down";
}

export default function Health() {
  const status = useRealtime((s) => s.status);
  const {
    fills,
    orders,
    signals,
    errors,
    equityCurve,
    runs,
    market,
    candles,
    tradeLevels,
    symbolSummaries,
  } = useRealtime();

  const health = useQuery({
    queryKey: ["health"],
    queryFn: endpoints.health,
    refetchInterval: 5000,
    retry: false,
  });
  const runtime = useQuery({
    queryKey: ["runtimeDiagnostics"],
    queryFn: endpoints.runtimeDiagnostics,
    refetchInterval: 5000,
    retry: false,
  });

  const candleCount = Object.values(candles).reduce(
    (acc, byInterval) => acc + Object.values(byInterval).reduce((sum, rows) => sum + rows.length, 0),
    0,
  );

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">API /health</span>
          <Badge tone={tone(health.data?.status)}>
            {health.data?.status ?? (health.isError ? "error" : "…")}
          </Badge>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Control bus</span>
          <Badge tone={tone(runtime.data?.control_bus)}>
            {runtime.data?.control_bus ?? (runtime.isError ? "unknown" : "…")}
          </Badge>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Realtime hub</span>
          <Badge tone={tone(runtime.data?.realtime_hub)}>
            {runtime.data?.realtime_hub ?? (runtime.isError ? "unknown" : "…")}
          </Badge>
        </Card>
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Realtime WS</span>
          <Badge tone={tone(status)}>{status}</Badge>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Realtime clients" value={runtime.data?.realtime_clients ?? "—"} />
        <Stat label="Run events" value={runs.length} />
        <Stat label="Equity points (live)" value={equityCurve.length} />
        <Stat label="Live errors" value={errors.length} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Market symbols" value={Object.keys(market).length} />
        <Stat label="Candles buffered" value={candleCount} />
        <Stat label="Trade levels" value={Object.keys(tradeLevels).length} />
        <Stat label="Symbol summaries" value={Object.keys(symbolSummaries).length} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Orders (live buffer)" value={orders.length} />
        <Stat label="Fills (live buffer)" value={fills.length} />
        <Stat label="Signals (live buffer)" value={signals.length} />
        <Card className="flex flex-col gap-1">
          <span className="text-xs uppercase text-muted">Mit jelent, ha minden 0?</span>
          <span className="text-sm text-muted">
            WS nyitva lehet akkor is, ha nincs futó engine. Indíts dry-run-t a Live Trading oldalon vagy a Strategies oldalon.
          </span>
        </Card>
      </div>

      {runtime.data?.control_bus === "unavailable" && (
        <Card className="border-down/40 bg-down/10">
          <div className="text-sm font-semibold text-down">Control bus unavailable</div>
          <p className="mt-1 text-sm text-muted">
            Az API nem tud start/stop parancsot küldeni Kafka/Redpanda felé. Ellenőrizd, hogy a redpanda service fut-e,
            és az API eléri-e a KAFKA_BOOTSTRAP_SERVERS címet.
          </p>
        </Card>
      )}
    </div>
  );
}
