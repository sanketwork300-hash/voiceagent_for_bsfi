import type { BackendFrame } from "@/types/events";

export type SocketStatus = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "unauthorized";

export interface ChatSocketOptions {
  url: string; // ws(s)://host/ws/chat/{session}?token=...
  onFrame: (f: BackendFrame) => void;
  onStatus: (s: SocketStatus) => void;
  maxRetries?: number;
  WebSocketImpl?: typeof WebSocket;
}

/** Thin, reconnecting WebSocket for chat streaming. Business state lives in the conversation reducer, not here. */
export class ChatSocket {
  private ws: WebSocket | null = null;
  private retries = 0;
  private closedByUser = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private queue: string[] = [];

  constructor(private opts: ChatSocketOptions) {}

  connect() {
    this.closedByUser = false;
    this.opts.onStatus(this.retries ? "reconnecting" : "connecting");
    const Impl = this.opts.WebSocketImpl ?? WebSocket;
    const ws = new Impl(this.opts.url);
    this.ws = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.opts.onStatus("open");
      for (const m of this.queue.splice(0)) ws.send(m);
    };
    ws.onmessage = (e) => {
      try {
        this.opts.onFrame(JSON.parse(String(e.data)) as BackendFrame);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = (e) => {
      if (this.closedByUser) return this.opts.onStatus("closed");
      if (e.code === 1008) return this.opts.onStatus("unauthorized"); // policy violation: bad/expired token
      if (this.retries >= (this.opts.maxRetries ?? 6)) return this.opts.onStatus("closed");
      this.retries += 1;
      this.opts.onStatus("reconnecting");
      this.timer = setTimeout(() => this.connect(), Math.min(8000, 400 * 2 ** this.retries));
    };
  }

  get isOpen() {
    return this.ws?.readyState === 1;
  }

  send(frame: { type: "message"; content: string; language?: string } | { type: "auth.otp"; otp: string } | { type: "interrupt" } | { type: "ping" }) {
    const raw = JSON.stringify(frame);
    if (this.isOpen) this.ws!.send(raw);
    else this.queue.push(raw);
  }

  close() {
    this.closedByUser = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
  }
}

export function chatSocketUrl(sessionId: string, token: string): string {
  const base = (process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8000").replace(/\/$/, "");
  return `${base}/ws/chat/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
}
