import type { WsMessage } from "@/types";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/live";

export type WsHandler = (message: WsMessage) => void;
export type SocketState = "live" | "reconnecting" | "offline";
type StateHandler = (state: SocketState) => void;

export class LiveSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<WsHandler>();
  private stateHandlers = new Set<StateHandler>();
  private subscribed = new Set<string>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private failures = 0;
  private url: string;

  constructor(url?: string) {
    this.url = url ?? WS_URL;
  }

  connect() {
    if (typeof window === "undefined") return;
    this.closed = false;
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.failures = 0;
      this.emitState("live");
      if (this.subscribed.size > 0) {
        this.send({ subscribe: Array.from(this.subscribed) });
      }
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage;
        this.handlers.forEach((h) => h(msg));
      } catch {
        // ignore malformed frames
      }
    };

    this.ws.onclose = () => {
      if (!this.closed) {
        this.failures += 1;
        this.emitState(this.failures >= 3 ? "offline" : "reconnecting");
        this.reconnectTimer = setTimeout(() => this.connect(), 3000);
      }
    };
  }

  onState(handler: StateHandler) {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  private emitState(state: SocketState) {
    this.stateHandlers.forEach((handler) => handler(state));
  }

  subscribe(channels: string[]) {
    channels.forEach((c) => this.subscribed.add(c));
    this.send({ subscribe: channels });
  }

  unsubscribe(channels: string[]) {
    channels.forEach((c) => this.subscribed.delete(c));
    this.send({ unsubscribe: channels });
  }

  onMessage(handler: WsHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private send(payload: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }
}

export async function openWallSocket(): Promise<LiveSocket> {
  const tokenRes = await fetch("/api/auth/ws-token");
  if (!tokenRes.ok) {
    throw new Error("Not authenticated");
  }
  const body = (await tokenRes.json()) as { token: string };
  const base = (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/live").replace(
    /\/ws\/live\/?$/,
    "",
  );
  return new LiveSocket(`${base}/ws/wall?token=${encodeURIComponent(body.token)}`);
}
