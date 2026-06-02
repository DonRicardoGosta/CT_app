// Dry trading view: dry-run trades only, with a run picker. "Active" streams the
// live dry-run over the WebSocket; selecting a finished run replays it from the DB.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import TradingWorkspace from "@/pages/TradingWorkspace";
import RunReplay from "@/components/RunReplay";
import { endpoints } from "@/lib/api";
import { Card, Select } from "@/components/ui";
import { time } from "@/lib/format";

export default function DryTrading() {
  const [view, setView] = useState<string>("live");

  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => endpoints.runs(),
    refetchInterval: 10000,
  });

  const dryRuns = useMemo(
    () => (runs.data ?? []).filter((r) => r.mode === "dry_run"),
    [runs.data],
  );

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-3 py-3">
        <div>
          <h2 className="text-lg font-semibold">Dry Trading</h2>
          <p className="text-xs text-muted">
            Simulated runs only. View the active stream or replay a past run.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">Run</span>
          <Select value={view} onChange={(e) => setView(e.target.value)}>
            <option value="live">Active (live stream)</option>
            {dryRuns.map((r) => (
              <option key={r.id} value={r.id}>
                {r.strategy} · {time(r.started_at)} · {r.status} · {r.id.slice(0, 8)}
              </option>
            ))}
          </Select>
        </div>
      </Card>

      {view === "live" ? <TradingWorkspace mode="dry_run" /> : <RunReplay runId={view} />}
    </div>
  );
}
