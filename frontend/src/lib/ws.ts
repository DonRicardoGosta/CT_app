// Typed, auto-reconnecting WebSocket client for the realtime multiplexed stream.
// This is the FAST path: it never touches the database (REQ-008).

export type Channel =
  | "order"
  | "fill"
  | "position"
  | "signal"
  | "equity"
  | "error"
  | "run";

export interface RealtimeEvent {
  type: Channel | "hello";
  run_id?: string;
  [key: string]: unknown;
}

type Listener = (event: RealtimeEvent) => void;
export type Status = "connecting" | "open" | "closed";

export class RealtimeClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<(s: Status) => void>();
  private channels = new Set<Channel>();
  private runId: string | null = null;
  private backoff = 1000;
  private shouldRun = false;

  constructor(private url: string) {}

  onEvent(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStatus(fn: (s: Status) => void): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  connect() {
    this.shouldRun = true;
    this.open();
  }

  close() {
    this.shouldRun = false;
    this.ws?.close();
    this.ws = null;
  }

  subscribe(channels: Channel[], runId?: string | null) {
    channels.forEach((c) => this.channels.add(c));
    if (runId !== undefined) this.runId = runId;
    this.send({ action: "subscribe", channels, run_id: this.runId });
  }

  setRun(runId: string | null) {
    this.runId = runId;
    this.send({ action: "subscribe", channels: [...this.channels], run_id: runId });
  }

  private send(msg: unknown) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  private emitStatus(s: Status) {
    this.statusListeners.forEach((fn) => fn(s));
  }

  private open() {
    if (!this.shouldRun) return;
    this.emitStatus("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}${this.url}`);
    this.ws = ws;

    ws.onopen = () => {
      this.backoff = 1000;
      this.emitStatus("open");
      if (this.channels.size) {
        this.send({ action: "subscribe", channels: [...this.channels], run_id: this.runId });
      }
    };
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as RealtimeEvent;
        this.listeners.forEach((fn) => fn(data));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      this.emitStatus("closed");
      if (this.shouldRun) {
        setTimeout(() => this.open(), this.backoff);
        this.backoff = Math.min(this.backoff * 2, 15000);
      }
    };
    ws.onerror = () => ws.close();
  }
}

export const realtime = new RealtimeClient("/api/realtime/ws");
