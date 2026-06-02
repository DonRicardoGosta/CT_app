// Poll run row + errors and merge with realtime WS for a single run_id.
import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { endpoints } from "@/lib/api";
import { realtime } from "@/lib/ws";
import { useRealtime } from "@/store/realtime";

export function useRunMonitor(runId: string | null) {
  useEffect(() => {
    if (!runId) return;
    realtime.setRun(runId);
    realtime.subscribe(["run", "equity", "error"], runId);
    return () => {
      realtime.setRun(null);
    };
  }, [runId]);

  const runRow = useQuery({
    queryKey: ["run", runId],
    queryFn: async () => {
      const runs = await endpoints.runs();
      return runs.find((r) => r.id === runId) ?? null;
    },
    enabled: !!runId,
    refetchInterval: (q) => {
      const st = q.state.data?.status;
      if (!st || st === "running" || st === "started") return 1000;
      return 4000;
    },
  });

  const errors = useQuery({
    queryKey: ["errors", runId],
    queryFn: () => endpoints.errors(runId!),
    enabled: !!runId,
    refetchInterval: 2000,
  });

  const system = useQuery({
    queryKey: ["system-status"],
    queryFn: endpoints.systemStatus,
    refetchInterval: 8000,
  });

  const wsStatus = useRealtime((s) => s.status);
  const liveRuns = useRealtime((s) => s.runs);
  const liveErrors = useRealtime((s) => s.errors);
  const runEquity = useRealtime((s) => (runId ? s.runEquity[runId] : undefined));

  const liveRun = useMemo(
    () => liveRuns.find((r) => r.run_id === runId),
    [liveRuns, runId],
  );

  const mergedErrors = useMemo(() => {
    const db = errors.data ?? [];
    const live = liveErrors.filter((e) => !runId || e.run_id === runId);
    return [...live, ...db];
  }, [errors.data, liveErrors, runId]);

  const status =
    liveRun?.status?.toString() ??
    runRow.data?.status ??
    (runId && !runRow.data && runRow.isFetched ? "unknown" : null);

  return {
    runId,
    status,
    runRow: runRow.data,
    errors: mergedErrors,
    runEquity: runEquity ?? [],
    wsStatus,
    system: system.data,
    isLoadingRun: runRow.isLoading,
    refetchRun: runRow.refetch,
  };
}
