// App shell: left sidebar navigation + top bar with mode badge, connection
// status, total equity and a PANIC (stop-all) kill switch.
import { NavLink, useLocation } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Boxes,
  FlaskConical,
  Gauge,
  HeartPulse,
  LayoutDashboard,
  Settings,
  ShieldHalf,
} from "lucide-react";
import type { ReactNode } from "react";
import { useRealtime } from "@/store/realtime";
import { endpoints } from "@/lib/api";
import { Badge, Button } from "@/components/ui";
import { usd } from "@/lib/format";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/live", label: "Live Trading", icon: Activity },
  { to: "/strategies", label: "Strategies", icon: Boxes },
  { to: "/risk", label: "Risk & Capital", icon: ShieldHalf },
  { to: "/backtest", label: "Backtest", icon: FlaskConical },
  { to: "/analytics", label: "History & Analytics", icon: BarChart3 },
  { to: "/logs", label: "Logs & Errors", icon: AlertTriangle },
  { to: "/health", label: "System Health", icon: HeartPulse },
  { to: "/settings", label: "Settings", icon: Settings },
];

function ConnDot({ status }: { status: string }) {
  const tone = status === "open" ? "bg-up" : status === "connecting" ? "bg-warn" : "bg-down";
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className={`h-2 w-2 rounded-full ${tone}`} /> WS {status}
    </span>
  );
}

async function panicStopAll() {
  if (!confirm("Stop ALL running runs now?")) return;
  try {
    const runs = await endpoints.runs();
    const running = runs.filter((r) => r.status === "running" || r.status === "started");
    await Promise.all(running.map((r) => endpoints.stopRun(r.id)));
    alert(`Sent stop to ${running.length} run(s).`);
  } catch (e) {
    alert(`Failed: ${String(e)}`);
  }
}

export default function Layout({ children }: { children: ReactNode }) {
  const status = useRealtime((s) => s.status);
  const equity = useRealtime((s) => s.equity);
  const location = useLocation();

  return (
    <div className="flex h-full">
      <aside className="flex w-60 flex-col border-r border-border bg-panel">
        <div className="flex items-center gap-2 px-4 py-4">
          <Gauge className="h-6 w-6 text-accent" />
          <div className="leading-tight">
            <div className="text-sm font-semibold">Bitunix</div>
            <div className="text-xs text-muted">Trading Platform</div>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                  isActive ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel2 hover:text-text"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 text-xs text-muted">v0.1.0</div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border bg-panel/60 px-5 py-3 backdrop-blur">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium capitalize text-text">
              {NAV.find((n) => n.to === location.pathname)?.label ?? "Bitunix"}
            </span>
          </div>
          <div className="flex items-center gap-4">
            <ConnDot status={status} />
            <div className="text-sm">
              <span className="text-muted">Equity </span>
              <span className="num font-semibold">{equity ? usd(equity.equity) : "—"}</span>
            </div>
            <Badge tone="accent">{equity ? String((equity as any).mode).toUpperCase() : "IDLE"}</Badge>
            <Button variant="danger" onClick={panicStopAll}>
              PANIC: Stop all
            </Button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-auto p-5">{children}</main>
      </div>
    </div>
  );
}
