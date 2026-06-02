// Number/time formatting helpers.

export function num(value: unknown, digits = 2): string {
  const n = typeof value === "number" ? value : parseFloat(String(value ?? ""));
  if (!isFinite(n)) return "-";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function usd(value: unknown, digits = 2): string {
  const n = typeof value === "number" ? value : parseFloat(String(value ?? ""));
  if (!isFinite(n)) return "-";
  return `$${num(n, digits)}`;
}

export function pct(value: unknown, digits = 2): string {
  const n = typeof value === "number" ? value : parseFloat(String(value ?? ""));
  if (!isFinite(n)) return "-";
  return `${num(n, digits)}%`;
}

/** Convert ``<input type="datetime-local">`` value to ISO UTC for the API. */
export function toIsoDateTime(value: string): string | undefined {
  if (!value.trim()) return undefined;
  const normalized = value.includes("T") ? value : `${value}T00:00:00`;
  const d = new Date(normalized);
  if (isNaN(d.getTime())) return undefined;
  return d.toISOString();
}

export function time(value: unknown): string {
  if (!value) return "-";
  const d = new Date(String(value));
  if (isNaN(d.getTime())) return String(value);
  return d.toLocaleString();
}

export function shortId(value: string): string {
  return value.length > 10 ? `${value.slice(0, 8)}…` : value;
}

export function pnlClass(value: unknown): string {
  const n = typeof value === "number" ? value : parseFloat(String(value ?? ""));
  if (!isFinite(n) || n === 0) return "text-muted";
  return n > 0 ? "text-up" : "text-down";
}
