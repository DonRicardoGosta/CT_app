// Small, dependency-light UI primitives styled with Tailwind (shadcn-inspired).
import clsx from "clsx";
import type { ReactNode } from "react";

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={clsx("rounded-xl border border-border bg-panel p-4", className)}>{children}</div>
  );
}

export function CardTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <h3 className="text-sm font-semibold text-text">{children}</h3>
      {right}
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  className,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      <span className={clsx("num text-2xl font-semibold", className)}>{value}</span>
      {sub && <span className="text-xs text-muted">{sub}</span>}
    </Card>
  );
}

type Variant = "default" | "primary" | "danger" | "ghost" | "warn";
export function Button({
  children,
  onClick,
  variant = "default",
  type = "button",
  disabled,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: Variant;
  type?: "button" | "submit";
  disabled?: boolean;
  className?: string;
}) {
  const variants: Record<Variant, string> = {
    default: "bg-panel2 hover:bg-border text-text border border-border",
    primary: "bg-accent hover:bg-blue-500 text-white",
    danger: "bg-down hover:bg-red-500 text-white",
    warn: "bg-warn hover:bg-yellow-400 text-black",
    ghost: "hover:bg-panel2 text-muted",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={clsx(
        "rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40",
        variants[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Badge({ children, tone = "muted" }: { children: ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    up: "bg-up/15 text-up",
    down: "bg-down/15 text-down",
    accent: "bg-accent/15 text-accent",
    warn: "bg-warn/15 text-warn",
    muted: "bg-panel2 text-muted",
  };
  return (
    <span className={clsx("rounded px-2 py-0.5 text-xs font-medium", tones[tone] || tones.muted)}>
      {children}
    </span>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs uppercase text-muted">
            {head.map((h, i) => (
              <th key={i} className="px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className }: { children: ReactNode; className?: string }) {
  return <td className={clsx("px-3 py-2", className)}>{children}</td>;
}

export function Tr({ children }: { children: ReactNode }) {
  return <tr className="border-b border-border/50 hover:bg-panel2/50">{children}</tr>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center text-sm text-muted">{children}</div>;
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs text-muted">{label}</span>
      {children}
      {hint && <span className="text-[11px] leading-snug text-muted/80">{hint}</span>}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx(
        "rounded-lg border border-border bg-panel2 px-3 py-1.5 text-sm outline-none focus:border-accent",
        props.className,
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={clsx(
        "rounded-lg border border-border bg-panel2 px-3 py-1.5 text-sm outline-none focus:border-accent",
        props.className,
      )}
    />
  );
}
