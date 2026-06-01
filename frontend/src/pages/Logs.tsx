// Logs & errors: DB history + live WebSocket (runs, errors).
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { useRealtime } from "@/store/realtime";
import { Badge, Card, CardTitle, Empty, Input, Table, Td, Tr } from "@/components/ui";
import { time } from "@/lib/format";

function tone(sev: string) {
  if (sev === "error" || sev === "critical") return "down";
  if (sev === "warning" || sev === "warn") return "warn";
  return "muted";
}

type LogRow = {
  ts: unknown;
  kind: "error" | "run";
  source: string;
  severity: string;
  message: string;
  run_id?: string;
};

export default function Logs() {
  const { data, isError: dbError } = useQuery({
    queryKey: ["errors"],
    queryFn: () => endpoints.errors(),
    refetchInterval: 5000,
  });
  const liveErrors = useRealtime((s) => s.errors);
  const liveRuns = useRealtime((s) => s.runs);
  const wsStatus = useRealtime((s) => s.status);
  const [filter, setFilter] = useState("");

  const rows: LogRow[] = [
    ...liveRuns.map((e) => ({
      ts: e.ts,
      kind: "run" as const,
      source: String(e.strategy ?? "run"),
      severity: String(e.status ?? "info"),
      message: e.detail ? String(e.detail) : `Run ${e.status}`,
      run_id: e.run_id ? String(e.run_id) : undefined,
    })),
    ...liveErrors.map((e) => ({
      ts: e.ts,
      kind: "error" as const,
      source: String(e.source),
      severity: String(e.severity),
      message: String(e.message),
      run_id: e.run_id ? String(e.run_id) : undefined,
    })),
    ...(data ?? []).map((e: any) => ({
      ts: e.ts,
      kind: "error" as const,
      source: e.source,
      severity: e.severity,
      message: e.message,
      run_id: e.run_id,
    })),
  ]
    .filter((e) => {
      if (!filter) return true;
      const blob = `${e.source} ${e.severity} ${e.message} ${e.run_id ?? ""}`.toLowerCase();
      return blob.includes(filter.toLowerCase());
    })
    .sort((a, b) => new Date(String(b.ts)).getTime() - new Date(String(a.ts)).getTime());

  return (
    <Card>
      <CardTitle
        right={
          <div className="flex items-center gap-2">
            <Badge tone={wsStatus === "open" ? "up" : "down"}>WS {wsStatus}</Badge>
            <Badge tone="muted">{rows.length} entries</Badge>
          </div>
        }
      >
        Logs & events
      </CardTitle>

      {wsStatus !== "open" && (
        <p className="mb-3 text-sm text-warn">
          WebSocket is not connected — live events will not appear. Check System Health and that
          backend-api / Kafka are up.
        </p>
      )}
      {dbError && (
        <p className="mb-3 text-sm text-down">
          Could not load history from the database. Is PostgreSQL and db-writer running?
        </p>
      )}

      <div className="mb-3">
        <Input
          placeholder="Filter by source / severity / message / run id…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full"
        />
      </div>
      {!rows.length ? (
        <Empty>
          No log entries yet. Start a backtest or dry-run — run and error events will show here
          (live via WS, persisted via db-writer).
        </Empty>
      ) : (
        <Table head={["Time", "Kind", "Source", "Severity", "Message", "Run"]}>
          {rows.slice(0, 300).map((e, i) => (
            <Tr key={i}>
              <Td className="text-muted">{time(e.ts)}</Td>
              <Td>
                <Badge tone={e.kind === "run" ? "accent" : "muted"}>{e.kind}</Badge>
              </Td>
              <Td>{e.source}</Td>
              <Td>
                <Badge tone={tone(e.severity)}>{e.severity}</Badge>
              </Td>
              <Td className="max-w-md truncate">{e.message}</Td>
              <Td className="text-muted font-mono text-xs">
                {e.run_id ? String(e.run_id).slice(0, 8) : "—"}
              </Td>
            </Tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
