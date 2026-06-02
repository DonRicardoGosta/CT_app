// Run progress: status badge, pipeline hints, errors for one run_id.
import { Badge, Card, CardTitle, Empty, Table, Td, Tr } from "@/components/ui";
import { useRunMonitor } from "@/hooks/useRunMonitor";
import { time } from "@/lib/format";

function statusTone(status: string | null) {
  if (!status) return "muted";
  if (status === "finished") return "up";
  if (status === "failed") return "down";
  if (status === "running" || status === "started") return "warn";
  return "muted";
}

export default function RunStatusPanel({ runId }: { runId: string }) {
  const { status, errors, wsStatus, system, isLoadingRun, runRow } = useRunMonitor(runId);

  const pipelineOk = system?.control_bus && system?.realtime_hub;
  const showWorkerHint =
    status === "unknown" &&
    !isLoadingRun &&
    !runRow &&
    system &&
    !system.recent_runs?.some((r: { id: string }) => r.id === runId);

  return (
    <Card>
      <CardTitle
        right={
          <div className="flex items-center gap-2">
            <Badge tone={wsStatus === "open" ? "up" : "down"}>WS {wsStatus}</Badge>
            <Badge tone={statusTone(status)}>{status ?? "pending…"}</Badge>
          </div>
        }
      >
        Run status
      </CardTitle>
      <p className="mb-3 font-mono text-xs text-muted">{runId}</p>

      <div className="mb-3 grid grid-cols-2 gap-2 text-sm lg:grid-cols-4">
        <div>
          <div className="text-xs text-muted">Control bus (API → Kafka)</div>
          <Badge tone={system?.control_bus ? "up" : "down"}>
            {system?.control_bus ? "ok" : "unavailable"}
          </Badge>
        </div>
        <div>
          <div className="text-xs text-muted">Realtime hub</div>
          <Badge tone={system?.realtime_hub ? "up" : "warn"}>
            {system?.realtime_hub ? "ok" : "down"}
          </Badge>
        </div>
        <div>
          <div className="text-xs text-muted">DB run record</div>
          <Badge tone={runRow ? "up" : "warn"}>{runRow ? runRow.status : "not yet"}</Badge>
        </div>
        <div>
          <div className="text-xs text-muted">Live equity points</div>
          <span className="num text-sm">via WS + DB</span>
        </div>
      </div>

      {showWorkerHint && (
        <div className="mb-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          This run is not in the database yet. Is <strong>trading-worker</strong> running?
          {` `}
          <code className="text-xs">docker compose ps trading-worker</code>
          {!pipelineOk && " — control bus or realtime hub also looks unhealthy."}
        </div>
      )}

      {status === "finished" && (
        <p className="mb-2 text-sm text-muted">
          Backtest finished. If the equity chart is still empty, there may have been no historical
          bars for the chosen symbols and dates (see errors below).
        </p>
      )}

      {(status === "running" || status === "started" || !status) && (
        <p className="mb-2 text-sm text-muted">
          Processing… equity updates stream over WebSocket and are saved to the database by
          db-writer.
        </p>
      )}

      {errors.length === 0 ? (
        <Empty>No errors for this run yet</Empty>
      ) : (
        <Table head={["Time", "Source", "Severity", "Message"]}>
          {errors.slice(0, 20).map((e: any, i: number) => (
            <Tr key={i}>
              <Td className="text-muted">{time(e.ts)}</Td>
              <Td>{e.source}</Td>
              <Td>
                <Badge tone={e.severity === "error" ? "down" : "warn"}>{e.severity}</Badge>
              </Td>
              <Td className="max-w-lg truncate">{e.message}</Td>
            </Tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
