/**
 * DEMO voice transport (no LiveKit / STT / TTS vendor keys needed):
 *   browser SpeechRecognition → POST /voice/simulate (backend VoiceChannel → shared AgentRuntime) → speechSynthesis
 * The backend path is the real voice path minus audio transport. Production uses the LiveKit transport.
 */
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { AgentResponse } from "@/types/domain";
import type { VoiceEvent } from "./machine";

export interface TranscriptEntry { id: string; speaker: "customer" | "agent" | "system"; text: string; final: boolean; at: string }

export interface TransportCallbacks {
  onEvent: (e: VoiceEvent) => void;
  onTranscript: (t: TranscriptEntry) => void;
  onResponse: (r: AgentResponse) => void;
  onLevel?: (level: number) => void;
  onMicUnavailable?: () => void;
}

type SR = { start(): void; stop(): void; abort(): void; continuous: boolean; interimResults: boolean; lang: string;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null; onend: (() => void) | null; onspeechstart: (() => void) | null };

export function speechRecognitionAvailable(): boolean {
  return typeof window !== "undefined" && Boolean((window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown }).SpeechRecognition
    ?? (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition);
}

let n = 0;
const id = () => `v-${Date.now().toString(36)}-${n++}`;
const ts = () => new Date().toISOString();
const SPEECH_LANG: Record<string, string> = { en: "en-IN", hi: "hi-IN", mr: "mr-IN", ta: "ta-IN", te: "te-IN", bn: "bn-IN", kn: "kn-IN", gu: "gu-IN", pa: "pa-IN", ml: "ml-IN" };

export class BrowserVoiceTransport {
  private rec: SR | null = null;
  private speaking = false;
  private speakStartedAt = 0;
  private muted = false;
  private ended = false;
  private busy = false;
  private language = "en";
  private levelTimer: ReturnType<typeof setInterval> | null = null;
  private partialId: string | null = null;
  private micBlocked = false;
  private connected = false;

  constructor(private token: string, private cb: TransportCallbacks) {}

  async start() {
    this.cb.onEvent({ type: "CONNECT" });
    this.ended = false;
    try {
      // Same endpoint the LiveKit flow uses: attaches voice to the existing (e.g. chat) session.
      await apiClient.voice.start(this.token);
    } catch (e) {
      this.cb.onEvent({ type: "FAIL", error: e instanceof ApiError ? e.userMessage : "Couldn't start the call." });
      return;
    }
    this.connected = true;
    this.cb.onEvent({ type: "CONNECTED" });
    this.cb.onTranscript({ id: id(), speaker: "system", text: "Call connected", final: true, at: ts() });
    this.listen();
    this.cb.onEvent({ type: "AGENT_LISTENING" });
  }

  private listen() {
    if (!speechRecognitionAvailable() || this.ended) return;
    const Ctor = ((window as unknown as { SpeechRecognition?: new () => SR }).SpeechRecognition
      ?? (window as unknown as { webkitSpeechRecognition: new () => SR }).webkitSpeechRecognition);
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = SPEECH_LANG[this.language] ?? "en-IN";
    rec.onspeechstart = () => this.maybeBargeIn();
    rec.onresult = (e) => {
      if (this.muted) return;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const text = r[0].transcript.trim();
        if (!text) continue;
        this.maybeBargeIn();
        if (!this.partialId) this.partialId = id();
        this.cb.onTranscript({ id: this.partialId, speaker: "customer", text, final: r.isFinal, at: ts() });
        if (r.isFinal) {
          this.partialId = null;
          void this.utter(text);
        }
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture") {
        // keep the call: the customer can still type or use the keypad
        this.micBlocked = true;
        this.rec = null;
        this.cb.onMicUnavailable?.();
        this.cb.onTranscript({ id: id(), speaker: "system", text: "Microphone unavailable — type what you'd like to say instead", final: true, at: ts() });
      }
    };
    rec.onend = () => { if (!this.ended && !this.micBlocked && this.rec === rec) setTimeout(() => { try { rec.start(); } catch { /* already started */ } }, 250); };
    try { rec.start(); } catch { /* ignore */ }
    this.rec = rec;
  }

  /** Barge-in: the customer started talking while the agent speaks → stop TTS at once. */
  private maybeBargeIn() {
    if (this.speaking && Date.now() - this.speakStartedAt > 700) this.interrupt();
  }

  interrupt() {
    if (!this.speaking) return;
    window.speechSynthesis?.cancel();
    this.speaking = false;
    this.stopLevels();
    this.cb.onEvent({ type: "USER_SPEECH" });
    this.cb.onTranscript({ id: id(), speaker: "system", text: "Customer interruption — assistant stopped speaking", final: true, at: ts() });
  }

  /** Typed input / keypad goes through the same path as recognised speech. */
  async utter(text: string, opts: { display?: string } = {}) {
    if (this.ended || this.busy || !this.connected) return;
    if (opts.display) this.cb.onTranscript({ id: id(), speaker: "customer", text: opts.display, final: true, at: ts() });
    this.busy = true;
    this.cb.onEvent({ type: "AGENT_THINKING" });
    try {
      const { response, speech_text } = await apiClient.voice.simulate(this.token, text);
      if (response) {
        this.cb.onResponse(response);
        this.language = response.language || this.language;
        this.cb.onTranscript({ id: id(), speaker: "agent", text: response.text, final: true, at: ts() });
        if (speech_text) this.speak(speech_text, response.language);
        else this.cb.onEvent({ type: "AGENT_LISTENING" });
      } else {
        this.cb.onEvent({ type: "AGENT_LISTENING" });
      }
    } catch (e) {
      this.cb.onTranscript({ id: id(), speaker: "system", text: e instanceof ApiError && e.isUnavailable
        ? "The banking service is temporarily unavailable. Nothing was changed." : "That didn't go through. Please try again.", final: true, at: ts() });
      this.cb.onEvent({ type: "AGENT_LISTENING" });
    } finally {
      this.busy = false;
    }
  }

  private speak(text: string, language: string) {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    if (!synth) { this.cb.onEvent({ type: "AGENT_LISTENING" }); return; }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = SPEECH_LANG[language] ?? "en-IN";
    const voice = synth.getVoices().find((v) => v.lang === u.lang) ?? synth.getVoices().find((v) => v.lang.startsWith(language));
    if (voice) u.voice = voice;
    u.rate = 1.02;
    u.onstart = () => { this.speaking = true; this.speakStartedAt = Date.now(); this.cb.onEvent({ type: "AGENT_SPEAKING" }); this.startLevels(); };
    u.onend = () => { if (this.speaking) { this.speaking = false; this.stopLevels(); this.cb.onEvent({ type: "AGENT_LISTENING" }); } };
    u.onerror = () => { this.speaking = false; this.stopLevels(); this.cb.onEvent({ type: "AGENT_LISTENING" }); };
    synth.cancel();
    synth.speak(u);
    // headless/blocked audio: onstart may never fire; fall back to listening
    setTimeout(() => { if (!this.speaking && !this.ended && !this.busy) this.cb.onEvent({ type: "AGENT_LISTENING" }); }, 1500);
  }

  // speechSynthesis exposes no audio stream, so the waveform uses a speech-like envelope while speaking.
  private startLevels() {
    let t = 0;
    this.stopLevels();
    this.levelTimer = setInterval(() => { t += 1; this.cb.onLevel?.(0.35 + 0.35 * Math.abs(Math.sin(t / 2.3)) * (0.6 + 0.4 * Math.random())); }, 80);
  }
  private stopLevels() {
    if (this.levelTimer) clearInterval(this.levelTimer);
    this.levelTimer = null;
    this.cb.onLevel?.(0);
  }

  setMuted(m: boolean) {
    this.muted = m;
    if (m) this.rec?.stop();
    else if (!this.ended) { try { this.rec?.start(); } catch { this.listen(); } }
  }

  end() {
    this.ended = true;
    window.speechSynthesis?.cancel();
    this.speaking = false;
    this.stopLevels();
    const r = this.rec;
    this.rec = null;
    r?.abort();
    this.cb.onTranscript({ id: id(), speaker: "system", text: "Call ended", final: true, at: ts() });
    this.cb.onEvent({ type: "END" });
  }
}
