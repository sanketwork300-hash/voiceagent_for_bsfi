"use client";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { VOICE_TRANSPORT } from "@/config/env";
import { apiClient } from "@/lib/api/client";
import { BrowserVoiceTransport, type TranscriptEntry } from "@/lib/voice/browser-transport";
import { initialVoice, voiceReducer, type VoiceEvent } from "@/lib/voice/machine";
import { useCustomerSession } from "@/store/customer-session";
import type { AgentResponse, AuthState } from "@/types/domain";

export interface LiveKitControls { setMuted: (m: boolean) => void; sendDigits: (d: string) => void; end: () => void }

/**
 * Voice call state shared by both transports. Business state (auth level, pending action, handoff) is read back
 * from the backend session — the browser never infers it.
 */
export function useVoice(transport: "demo" | "livekit" = VOICE_TRANSPORT) {
  const { sessionId, token, setAuthState, update } = useCustomerSession();
  const [machine, dispatch] = useReducer(voiceReducer, initialVoice);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [level, setLevel] = useState(0);
  const [livekit, setLivekit] = useState<{ url: string; token: string } | null>(null);
  const [lastResponse, setLastResponse] = useState<AgentResponse | null>(null);
  const [micUnavailable, setMicUnavailable] = useState(false);
  const demo = useRef<BrowserVoiceTransport | null>(null);
  const lk = useRef<LiveKitControls | null>(null);
  const live = !["idle", "ended", "error"].includes(machine.state);

  const onTranscript = useCallback((t: TranscriptEntry) => {
    setTranscript((prev) => {
      const i = prev.findIndex((p) => p.id === t.id);
      if (i < 0) return [...prev, t];
      const next = prev.slice();
      next[i] = t;
      return next;
    });
  }, []);

  const session = useQuery({
    queryKey: ["voice-session", sessionId],
    queryFn: () => apiClient.sessions.get(sessionId!, token!),
    enabled: Boolean(sessionId && token && live),
    refetchInterval: 2500,
  });
  const prevAuth = useRef<AuthState | null>(null);
  useEffect(() => {
    const s = session.data;
    if (!s) return;
    setAuthState(s.authentication_state);
    update({ pending_action: s.pending_action, status: s.status, handoff_id: s.handoff_id, active_channels: s.active_channels });
    if (s.status === "HANDED_OFF") dispatch({ type: "HANDOFF" });
    dispatch({ type: s.pending_action?.decision === "REQUIRE_AUTH" ? "AUTH_REQUIRED" : "AUTH_RESOLVED" });
    if (prevAuth.current && prevAuth.current !== s.authentication_state) {
      onTranscript({ id: `auth-${s.authentication_state}`, speaker: "system", text: `Verification updated: ${s.authentication_state.replace(/_/g, " ").toLowerCase()}`, final: true, at: new Date().toISOString() });
    }
    prevAuth.current = s.authentication_state;
  }, [session.data, setAuthState, update, onTranscript]);

  const onResponse = useCallback((r: AgentResponse) => {
    setLastResponse(r);
    if (r.pending_action?.decision === "REQUIRE_AUTH") dispatch({ type: "AUTH_REQUIRED" });
    if (r.handoff) dispatch({ type: "HANDOFF" });
    void session.refetch();
  }, [session]);

  const start = useCallback(async () => {
    if (!token) return;
    setTranscript([]);
    if (transport === "demo") {
      setMicUnavailable(false);
      const t = new BrowserVoiceTransport(token, { onEvent: dispatch, onTranscript, onResponse, onLevel: setLevel, onMicUnavailable: () => setMicUnavailable(true) });
      demo.current = t;
      await t.start();
    } else {
      dispatch({ type: "CONNECT" });
      try {
        const v = await apiClient.voice.start(token);
        setLivekit({ url: v.livekit_url, token: v.participant_token });
      } catch {
        dispatch({ type: "FAIL", error: "Couldn't start the call." });
      }
    }
  }, [token, transport, onTranscript, onResponse]);

  const end = useCallback(() => {
    if (transport === "demo") demo.current?.end();
    else { lk.current?.end(); setLivekit(null); dispatch({ type: "END" }); }
  }, [transport]);

  const toggleMute = useCallback(() => {
    const m = !machine.muted;
    dispatch({ type: m ? "MUTE" : "UNMUTE" });
    if (transport === "demo") demo.current?.setMuted(m);
    else lk.current?.setMuted(m);
  }, [machine.muted, transport]);

  /** Keypad digits: an OTP is never shown in the transcript. */
  const sendDigits = useCallback((digits: string) => {
    const display = `Keypad entry ${"•".repeat(digits.length)}`;
    if (transport === "demo") void demo.current?.utter(digits, { display });
    else { lk.current?.sendDigits(digits); onTranscript({ id: `kp-${Date.now()}`, speaker: "customer", text: display, final: true, at: new Date().toISOString() }); }
  }, [transport, onTranscript]);

  const say = useCallback((text: string) => { if (transport === "demo") void demo.current?.utter(text, { display: text }); }, [transport]);
  const interrupt = useCallback(() => { if (transport === "demo") demo.current?.interrupt(); }, [transport]);

  useEffect(() => () => { demo.current?.end(); }, []);

  return {
    machine, dispatch: dispatch as (e: VoiceEvent) => void, transcript, onTranscript, level, setLevel, live, transport,
    livekit, registerLiveKit: (c: LiveKitControls | null) => { lk.current = c; },
    start, end, toggleMute, sendDigits, say, interrupt, lastResponse, micUnavailable, session: session.data, ready: Boolean(sessionId && token),
  };
}
