/**
 * Voice UI state machine (spec §54). Transport-agnostic: LiveKit agent states and the browser demo transport both
 * translate their signals into these events.
 *
 *   idle → connecting → connected → listening ⇄ processing → speaking → listening
 *   speaking → interrupted → listening          any → handoff        any → ended
 */
export type VoiceState =
  | "idle" | "connecting" | "connected" | "listening" | "processing" | "speaking" | "interrupted"
  | "auth_required" | "handoff" | "ended" | "error";

export interface VoiceContext {
  state: VoiceState;
  muted: boolean;
  /** Waiting for an OTP / step-up. A flag, not a state: the agent keeps listening while it is set. */
  authRequired: boolean;
  previous: VoiceState | null;
  error: string | null;
  interruptions: number;
  startedAt: number | null;
  endedAt: number | null;
}

export type VoiceEvent =
  | { type: "CONNECT" }
  | { type: "CONNECTED" }
  | { type: "AGENT_LISTENING" }
  | { type: "AGENT_THINKING" }
  | { type: "AGENT_SPEAKING" }
  | { type: "USER_SPEECH" } // user started talking (barge-in if the agent is speaking)
  | { type: "AUTH_REQUIRED" }
  | { type: "AUTH_RESOLVED" }
  | { type: "HANDOFF" }
  | { type: "MUTE" }
  | { type: "UNMUTE" }
  | { type: "END" }
  | { type: "FAIL"; error: string }
  | { type: "RESET" };

export const initialVoice: VoiceContext = { state: "idle", muted: false, authRequired: false, previous: null, error: null, interruptions: 0, startedAt: null, endedAt: null };

const LIVE: VoiceState[] = ["connected", "listening", "processing", "speaking", "interrupted"];

function go(ctx: VoiceContext, state: VoiceState): VoiceContext {
  return state === ctx.state ? ctx : { ...ctx, previous: ctx.state, state };
}

export function voiceReducer(ctx: VoiceContext, ev: VoiceEvent): VoiceContext {
  if (ev.type === "RESET") return initialVoice;
  if (ctx.state === "ended" && ev.type !== "CONNECT") return ctx;
  switch (ev.type) {
    case "CONNECT":
      return { ...initialVoice, state: "connecting", previous: ctx.state, startedAt: Date.now() };
    case "CONNECTED":
      return ctx.state === "connecting" ? go(ctx, "connected") : ctx;
    case "AGENT_LISTENING":
      return LIVE.includes(ctx.state) ? go(ctx, "listening") : ctx;
    case "AGENT_THINKING": // e.g. verifying an OTP that was just entered
      return LIVE.includes(ctx.state) ? go(ctx, "processing") : ctx;
    case "AGENT_SPEAKING": // during a transfer the announcement plays but the call stays "Transferring"
      return LIVE.includes(ctx.state) ? go(ctx, "speaking") : ctx;
    case "USER_SPEECH":
      if (ctx.muted) return ctx;
      if (ctx.state === "speaking") return { ...go(ctx, "interrupted"), interruptions: ctx.interruptions + 1 };
      return ctx.state === "interrupted" || LIVE.includes(ctx.state) ? go(ctx, "listening") : ctx;
    case "AUTH_REQUIRED":
      return LIVE.includes(ctx.state) ? { ...ctx, authRequired: true } : ctx;
    case "AUTH_RESOLVED":
      return { ...ctx, authRequired: false };
    case "HANDOFF":
      return ctx.state === "idle" ? ctx : { ...go(ctx, "handoff"), authRequired: false };
    case "MUTE":
      return { ...ctx, muted: true };
    case "UNMUTE":
      return { ...ctx, muted: false };
    case "END":
      return ctx.state === "idle" ? ctx : { ...go(ctx, "ended"), endedAt: Date.now() };
    case "FAIL":
      return { ...go(ctx, "error"), error: ev.error };
  }
}

export interface VoiceStateCopy { label: string; hint: string; tone: "neutral" | "voice" | "warning" | "danger" | "info" }

export const VOICE_COPY: Record<VoiceState, VoiceStateCopy> = {
  idle: { label: "Ready", hint: "Start a call to talk to the assistant.", tone: "neutral" },
  connecting: { label: "Connecting", hint: "Setting up a secure audio line.", tone: "neutral" },
  connected: { label: "Connected", hint: "Say hello, in any language you like.", tone: "voice" },
  listening: { label: "Listening", hint: "Go ahead, I'm listening.", tone: "voice" },
  processing: { label: "Thinking", hint: "Working on your request.", tone: "info" },
  speaking: { label: "Speaking", hint: "Start talking any time to interrupt.", tone: "voice" },
  interrupted: { label: "Interrupted", hint: "Stopped speaking. I'm listening.", tone: "voice" },
  auth_required: { label: "Verification required", hint: "Say or type the one-time password sent to your phone.", tone: "warning" },
  handoff: { label: "Transferring", hint: "Connecting you to a specialist with the context of this call.", tone: "info" },
  ended: { label: "Call ended", hint: "The conversation is saved; you can continue in chat.", tone: "neutral" },
  error: { label: "Call problem", hint: "The audio connection failed. Nothing was changed on your account.", tone: "danger" },
};

export function displayState(ctx: VoiceContext): VoiceStateCopy {
  if (ctx.muted && LIVE.includes(ctx.state)) return { label: "Muted", hint: "Your microphone is off. Unmute to talk.", tone: "neutral" };
  if (ctx.authRequired && ["connected", "listening", "interrupted"].includes(ctx.state)) return VOICE_COPY.auth_required;
  return VOICE_COPY[ctx.state];
}

/** Map LiveKit Agents' participant state (`lk.agent.state`) to machine events. */
export function eventForAgentState(s: string): VoiceEvent | null {
  switch (s) {
    case "listening":
    case "idle":
      return { type: "AGENT_LISTENING" };
    case "thinking":
      return { type: "AGENT_THINKING" };
    case "speaking":
      return { type: "AGENT_SPEAKING" };
    case "failed":
      return { type: "FAIL", error: "The voice agent couldn't start." };
    default:
      return null;
  }
}
