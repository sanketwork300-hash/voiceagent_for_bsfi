import { describe, expect, it } from "vitest";
import { displayState, eventForAgentState, initialVoice, voiceReducer, type VoiceEvent } from "@/lib/voice/machine";

const run = (...evs: VoiceEvent[]) => evs.reduce(voiceReducer, initialVoice);

describe("voice state machine", () => {
  it("follows the happy path idle → connecting → connected → listening → processing → speaking → listening", () => {
    const states: string[] = [];
    let s = initialVoice;
    for (const e of ["CONNECT", "CONNECTED", "AGENT_LISTENING", "AGENT_THINKING", "AGENT_SPEAKING", "AGENT_LISTENING"] as const) {
      s = voiceReducer(s, { type: e });
      states.push(s.state);
    }
    expect(states).toEqual(["connecting", "connected", "listening", "processing", "speaking", "listening"]);
  });

  it("barge-in while speaking goes to interrupted and counts it", () => {
    const s = run({ type: "CONNECT" }, { type: "CONNECTED" }, { type: "AGENT_SPEAKING" }, { type: "USER_SPEECH" });
    expect(s.state).toBe("interrupted");
    expect(s.interruptions).toBe(1);
    expect(voiceReducer(s, { type: "AGENT_LISTENING" }).state).toBe("listening");
  });

  it("user speech when not speaking is not an interruption; muted speech is ignored", () => {
    expect(run({ type: "CONNECT" }, { type: "CONNECTED" }, { type: "AGENT_LISTENING" }, { type: "USER_SPEECH" }).interruptions).toBe(0);
    expect(run({ type: "CONNECT" }, { type: "CONNECTED" }, { type: "AGENT_SPEAKING" }, { type: "MUTE" }, { type: "USER_SPEECH" }).state).toBe("speaking");
  });

  it("handoff and end are reachable from any live state; ended is terminal", () => {
    const h = run({ type: "CONNECT" }, { type: "CONNECTED" }, { type: "AGENT_THINKING" }, { type: "HANDOFF" });
    expect(h.state).toBe("handoff");
    expect(voiceReducer(h, { type: "AGENT_SPEAKING" }).state).toBe("handoff"); // announcement plays, still transferring
    const e = voiceReducer(h, { type: "END" });
    expect(e.state).toBe("ended");
    expect(voiceReducer(e, { type: "AGENT_SPEAKING" }).state).toBe("ended");
    expect(voiceReducer(e, { type: "CONNECT" }).state).toBe("connecting");
  });

  it("authentication required is a persistent flag shown while listening", () => {
    const s = run({ type: "CONNECT" }, { type: "CONNECTED" }, { type: "AGENT_SPEAKING" }, { type: "AUTH_REQUIRED" }, { type: "AGENT_LISTENING" });
    expect(s.state).toBe("listening");
    expect(displayState(s).label).toBe("Verification required");
    expect(displayState(voiceReducer(s, { type: "AUTH_RESOLVED" })).label).toBe("Listening");
  });

  it("muted display takes precedence and failures carry a message", () => {
    expect(displayState(run({ type: "CONNECT" }, { type: "CONNECTED" }, { type: "MUTE" })).label).toBe("Muted");
    const f = run({ type: "CONNECT" }, { type: "FAIL", error: "x" });
    expect(f.state).toBe("error");
    expect(f.error).toBe("x");
  });

  it("maps LiveKit agent states", () => {
    expect(eventForAgentState("thinking")).toEqual({ type: "AGENT_THINKING" });
    expect(eventForAgentState("speaking")).toEqual({ type: "AGENT_SPEAKING" });
    expect(eventForAgentState("initializing")).toBeNull();
  });
});
