// Compact coin card for the watchlist strip: sparkline + price + status badge.
import clsx from "clsx";
import Sparkline from "@/components/Sparkline";
import { Badge } from "@/components/ui";
import type { SymbolSummary } from "@/store/realtime";
import { pnlClass, usd } from "@/lib/format";

function statusTone(status?: string, side?: string) {
  if (status === "in_position") return side === "short" ? "down" : "up";
  if (status === "pending_order") return "warn";
  return "muted";
}

export default function MiniCoinCard({
  symbol,
  active,
  price,
  spark,
  summary,
  onClick,
}: {
  symbol: string;
  active: boolean;
  price?: number;
  spark: number[];
  summary?: SymbolSummary;
  onClick: () => void;
}) {
  const side = summary?.position_side;
  const up = spark.length < 2 || spark[spark.length - 1] >= spark[0];
  const label =
    summary?.status === "in_position"
      ? (side ?? "in").toUpperCase()
      : summary?.status === "pending_order"
        ? "PENDING"
        : "SCANNING";

  return (
    <button
      onClick={onClick}
      className={clsx(
        "min-w-[150px] flex-1 rounded-xl border bg-panel p-3 text-left transition-colors",
        active ? "border-accent ring-1 ring-accent/40" : "border-border hover:bg-panel2",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{symbol}</span>
        <Badge tone={statusTone(summary?.status, side)}>{label}</Badge>
      </div>
      <div className="num mt-1 text-lg font-semibold">{price != null ? usd(price) : "—"}</div>
      <div className="mt-1 h-11">
        {spark.length > 1 ? (
          <Sparkline values={spark} up={up} height={44} />
        ) : (
          <div className="h-full" />
        )}
      </div>
      <div className="mt-1 flex items-center justify-between text-xs">
        <span className="text-muted">
          {summary?.step_count != null
            ? `step ${summary.step_count}/${summary.max_steps ?? "—"}`
            : ""}
        </span>
        <span className={`num ${pnlClass(summary?.unrealized_pnl)}`}>
          {summary?.unrealized_pnl != null ? usd(summary.unrealized_pnl) : ""}
        </span>
      </div>
    </button>
  );
}
