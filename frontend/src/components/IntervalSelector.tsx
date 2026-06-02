// Candle interval toggle for the big chart.
import clsx from "clsx";

export const INTERVALS = ["1m", "5m", "15m", "1h"] as const;
export type Interval = (typeof INTERVALS)[number];

export default function IntervalSelector({
  value,
  onChange,
}: {
  value: Interval;
  onChange: (i: Interval) => void;
}) {
  return (
    <div className="flex items-center gap-1 rounded-lg border border-border bg-panel2 p-0.5">
      {INTERVALS.map((i) => (
        <button
          key={i}
          onClick={() => onChange(i)}
          className={clsx(
            "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
            value === i ? "bg-accent text-white" : "text-muted hover:text-text",
          )}
        >
          {i}
        </button>
      ))}
    </div>
  );
}
