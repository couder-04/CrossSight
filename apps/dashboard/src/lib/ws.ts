import type { WsMessage } from "@/types";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/live";

export type WsHandler = (message: WsMessage) => void;

export class LiveSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<WsHandler>();
  private subscribed = new Set<string>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;

  connect() {
    if (typeof window === "undefined") return;
    this.closed = false;
    this.ws = new WebSocket(WS_URL);

    this.ws.onopen = () => {
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
        this.reconnectTimer = setTimeout(() => this.connect(), 3000);
      }
    };
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
