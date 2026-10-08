"use client";
import { useIsSpeaking, useLocalParticipant, useMultibandTrackVolume, useRoomContext, useTranscriptions, useVoiceAssistant } from "@livekit/components-react";
import { useEffect, useRef } from "react";
import type { LiveKitControls } from "@/hooks/use-voice";
import type { TranscriptEntry } from "@/lib/voice/browser-transport";
import { eventForAgentState, type VoiceEvent } from "@/lib/voice/machine";

/**
 * Lives inside <LiveKitRoom>. Translates LiveKit Agents signals into the shared voice machine:
 * `lk.agent.state` → listening/thinking/speaking, local speech during agent speech → barge-in,
 * `lk.transcription` text streams → partial/final transcript lines.
 */
export function LiveKitBridge({ onEvent, onTranscript, onLevel, register }: {
  onEvent: (e: VoiceEvent) => void; onTranscript: (t: TranscriptEntry) => void; onLevel: (l: number) => void;
  register: (c: LiveKitControls | null) => void;
}) {
  const room = useRoomContext();
  const { state, audioTrack } = useVoiceAssistant();
  const { localParticipant } = useLocalParticipant();
  const userSpeaking = useIsSpeaking(localParticipant);
  const transcriptions = useTranscriptions();
  const bands = useMultibandTrackVolume(audioTrack, { bands: 1 });
  const prevState = useRef<string | null>(null);

  useEffect(() => {
    if (state === "connecting" || state === "initializing") return;
    if (prevState.current === null && state !== "disconnected") onEvent({ type: "CONNECTED" });
    const ev = eventForAgentState(state);
    if (ev) onEvent(ev);
    prevState.current = state;
  }, [state, onEvent]);

  useEffect(() => {
    if (userSpeaking) onEvent({ type: "USER_SPEECH" }); // the agent stops TTS server-side; the UI reflects it
  }, [userSpeaking, onEvent]);

  useEffect(() => { onLevel(bands[0] ?? 0); }, [bands, onLevel]);

  useEffect(() => {
    for (const t of transcriptions) {
      const final = t.streamInfo.attributes?.["lk.transcription_final"] === "true";
      const segment = t.streamInfo.attributes?.["lk.segment_id"] ?? t.streamInfo.id;
      const speaker = t.participantInfo.identity === localParticipant.identity ? "customer" : "agent";
      onTranscript({ id: `lk-${segment}`, speaker, text: t.text, final, at: new Date(t.streamInfo.timestamp).toISOString() });
    }
  }, [transcriptions, localParticipant.identity, onTranscript]);

  useEffect(() => {
    register({
      setMuted: (m) => void localParticipant.setMicrophoneEnabled(!m),
      // LiveKit Agents accepts text input on the `lk.chat` topic: keypad digits reach the same runtime turn path.
      sendDigits: (d) => void localParticipant.sendText(d, { topic: "lk.chat" }),
      end: () => void room.disconnect(),
    });
    return () => register(null);
  }, [register, localParticipant, room]);

  return null;
}
