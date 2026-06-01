// Logs & errors (DB-backed) merged with the live error stream (WS).
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

export default function Logs() {
  const { data } = useQuery({ queryKey: ["errors"], queryFn: endpoints.errors, refetchInterval: 5000 });
  const liveErrors = useRealtime((s) => s.errors);
  const [filter, setFilter] = useState("");

  const rows = [...liveErrors, ...(data ?? [])].filter((e: any) => {
    if (!filter) return true;
    const blob = `${e.source} ${e.severity} ${e.message}`.toLowerCase();
    return blob.includes(filter.toLowerCase());
  });

  return (
    <Card>
      <CardTitle right={<Badge tone="muted">{rows.length} entries</Badge>}>
        Errors & events (everything, from everywhere)
      </CardTitle>
      <div className="mb-3">
        <Input
          placeholder="Filter by source / severity / message…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full"
        />
      </div>
      {!rows.length ? (
        <Empty>No log entries</Empty>
      ) : (
        <Table head={["Time", "Source", "Severity", "Message", "Run"]}>
          {rows.slice(0, 300).map((e: any, i: number) => (
            <Tr key={i}>
              <Td className="text-muted">{time(e.ts)}</Td>
              <Td>{e.source}</Td>
              <Td>
                <Badge tone={tone(e.severity)}>{e.severity}</Badge>
              </Td>
              <Td className="max-w-md truncate">{e.message}</Td>
              <Td className="text-muted">{e.run_id ? String(e.run_id).slice(0, 8) : "—"}</Td>
            </Tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
