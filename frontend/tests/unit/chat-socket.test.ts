import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatSocket, chatSocketUrl } from "@/lib/websocket/chat-socket";

class FakeWS {
  static instances: FakeWS[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor(public url: string) { FakeWS.instances.push(this); }
  send(d: string) { this.sent.push(d); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.(); }
}

afterEach(() => { FakeWS.instances = []; vi.useRealTimers(); });

describe("ChatSocket", () => {
  it("queues frames until open, then flushes", () => {
    const frames: unknown[] = [];
    const s = new ChatSocket({ url: "ws://x", onFrame: (f) => frames.push(f), onStatus: () => {}, WebSocketImpl: FakeWS as unknown as typeof WebSocket });
    s.connect();
    s.send({ type: "message", content: "hi" });
    const ws = FakeWS.instances[0];
    expect(ws.sent).toHaveLength(0);
    ws.open();
    expect(JSON.parse(ws.sent[0])).toEqual({ type: "message", content: "hi" });
    ws.onmessage?.({ data: JSON.stringify({ type: "pong" }) });
    ws.onmessage?.({ data: "not json" });
    expect(frames).toEqual([{ type: "pong" }]);
  });

  it("reconnects with backoff, but stops on authorization failure (1008)", () => {
    vi.useFakeTimers();
    const statuses: string[] = [];
    const s = new ChatSocket({ url: "ws://x", onFrame: () => {}, onStatus: (x) => statuses.push(x), WebSocketImpl: FakeWS as unknown as typeof WebSocket });
    s.connect();
    FakeWS.instances[0].onclose?.({ code: 1006 });
    expect(statuses.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(10_000);
    expect(FakeWS.instances).toHaveLength(2);
    FakeWS.instances[1].onclose?.({ code: 1008 });
    expect(statuses.at(-1)).toBe("unauthorized");
    vi.advanceTimersByTime(10_000);
    expect(FakeWS.instances).toHaveLength(2);
  });

  it("encodes session id and token in the URL", () => {
    expect(chatSocketUrl("a/b", "t&k")).toBe("ws://localhost:8000/ws/chat/a%2Fb?token=t%26k");
  });
});
