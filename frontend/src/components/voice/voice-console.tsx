"use client";
import { LiveKitRoom, RoomAudioRenderer } from "@livekit/components-react";
import { Grid3x3, Hand, Mic, MicOff, MessageSquareText, PhoneOff, Phone, Send } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { SessionGate } from "@/components/chat/session-gate";
import { HandoffBanner } from "@/components/handoff/handoff-banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { useVoice } from "@/hooks/use-voice";
import { LiveKitBridge } from "@/lib/livekit/bridge";
import { speechRecognitionAvailable } from "@/lib/voice/browser-transport";
import { displayState } from "@/lib/voice/machine";
import { sessionUsable, useCustomerSession } from "@/store/customer-session";
import { AUTH_ORDER, type AuthState } from "@/types/domain";
import { languageLabel } from "@/utils/format";
import { cn } from "@/utils/cn";
import { LiveTranscript } from "./live-transcript";
import { Keypad } from "./keypad";
import { Waveform } from "./waveform";

const TONE_DOT = { neutral: "bg-subtle", voice: "bg-voice", warning: "bg-warning", danger: "bg-danger", info: "bg-info" } as const;

function IdentityStrip({ state }: { state: AuthState }) {
  const lvl = AUTH_ORDER.indexOf(state);
  const steps: [AuthState, string][] = [["IDENTIFIED", "Identified"], ["FULLY_AUTHENTICATED", "Verified"], ["TRANSACTION_AUTHENTICATED", "Transaction verified"]];
  return (
    <div className="grid justify-items-center gap-1.5">
      <ul className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-meta" aria-label="Identity status">
        {steps.map(([s, label]) => {
          const on = lvl >= AUTH_ORDER.indexOf(s);
          return <li key={s} className={cn("inline-flex items-center gap-1.5", on ? "text-foreground" : "text-subtle")}>
            <span aria-hidden className={cn("size-1.5 rounded-full", on ? "bg-success" : "border border-border-strong")} />{label}<span className="sr-only">{on ? " (yes)" : " (no)"}</span>
          </li>;
        })}
      </ul>
      <p className="text-meta text-subtle">Recognising your voice never authorises payments on its own.</p>
    </div>
  );
}

export function VoiceConsole({ audience, chatHref }: { audience: "customer" | "operator"; chatHref: string }) {
  const s = useCustomerSession();
  if (!s.sessionId || !sessionUsable(s)) return <SessionGate audience={audience} />;
  return <VoiceInner audience={audience} chatHref={chatHref} />;
}

function VoiceInner({ audience, chatHref }: { audience: "customer" | "operator"; chatHref: string }) {
  const v = useVoice();
  const { session } = useCustomerSession();
  const [keypad, setKeypad] = useState(false);
  const [typed, setTyped] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const copy = displayState(v.machine);
  const canSpeak = speechRecognitionAvailable() && !v.micUnavailable;
  const connected = v.live && v.machine.state !== "connecting";

  useEffect(() => {
    if (!v.live || !v.machine.startedAt) return;
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - v.machine.startedAt!) / 1000)), 1000);
    return () => clearInterval(t);
  }, [v.live, v.machine.startedAt]);

  const caption = useMemo(() => {
    const last = [...v.transcript].reverse().find((t) => t.speaker !== "system");
    return last?.text ?? (v.machine.state === "idle" ? "How can I help you today?" : "");
  }, [v.transcript, v.machine.state]);
  const handoff = v.machine.state === "handoff" ? { status: "requested" as const } : { status: "none" as const };
  const mmss = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="grid-lines relative flex min-h-0 flex-1 flex-col items-center justify-between gap-6 overflow-y-auto px-4 py-6">
        <div className="grid justify-items-center gap-2 text-center">
          <p className="text-small text-muted">{audience === "operator" ? "Customer support · voice" : "Demo Bank · call"}</p>
          <p className="flex items-center gap-2 text-lead font-medium text-strong" role="status" aria-live="polite">
            <span aria-hidden className={cn("size-2 rounded-full", TONE_DOT[copy.tone], v.live && "animate-pulse-dot")} />{copy.label}
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            {v.live && <Badge tone="neutral"><span className="tabular font-mono">{mmss}</span></Badge>}
            {session?.language && session.language !== "en" && <Badge tone="neutral">Detected: {languageLabel(session.language)}</Badge>}
            <Badge tone={v.transport === "demo" ? "warning" : "neutral"}>{v.transport === "demo" ? "Demo audio · browser speech" : "LiveKit"}</Badge>
          </div>
        </div>

        <div className="grid justify-items-center gap-5">
          <Waveform state={v.machine.state} level={v.level} muted={v.machine.muted} authRequired={v.machine.authRequired} />
          <p className={cn("min-h-12 max-w-xl text-balance text-center text-lead", v.machine.state === "speaking" ? "text-strong" : "text-muted")}>
            {caption ? `“${caption}”` : ""}
          </p>
          <p className="text-small text-muted">{copy.hint}</p>
          {v.machine.error && <p role="alert" className="max-w-md text-center text-small text-danger">{v.machine.error}</p>}
        </div>

        <div className="grid w-full max-w-xl justify-items-center gap-4">
          {v.live && <IdentityStrip state={session?.authentication_state ?? "UNAUTHENTICATED"} />}
          <HandoffBanner handoff={handoff} />
          {connected && v.transport === "demo" && (
            <form className="flex w-full gap-2" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { v.say(typed.trim()); setTyped(""); } }}>
              <label htmlFor="voice-typed" className="sr-only">Type what you would say</label>
              <input id="voice-typed" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={canSpeak ? "Or type what you'd say…" : "Microphone unavailable — type what you'd say"}
                className="h-9 flex-1 rounded-control border border-border-strong bg-surface px-3 text-small text-foreground placeholder:text-subtle focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-strong/60" />
              <Button type="submit" size="icon" aria-label="Say this" disabled={!typed.trim()}><Send /></Button>
            </form>
          )}
          {!v.live ? (
            <div className="flex flex-wrap justify-center gap-2">
              <Button variant="primary" size="lg" onClick={() => void v.start()} disabled={!v.ready}><Phone />{v.machine.state === "ended" ? "Call again" : "Start call"}</Button>
              {v.machine.state === "ended" && <Button size="lg" asChild><Link href={chatHref}><MessageSquareText />Continue in chat</Link></Button>}
            </div>
          ) : (
            <div className="flex items-center gap-3" role="group" aria-label="Call controls">
              <Button variant="secondary" size="lg" aria-pressed={v.machine.muted} onClick={v.toggleMute} className="w-28">
                {v.machine.muted ? <MicOff /> : <Mic />}{v.machine.muted ? "Unmute" : "Mute"}
              </Button>
              <Button variant="danger" size="lg" onClick={v.end} className="w-32"><PhoneOff />End call</Button>
              <Button variant="secondary" size="lg" onClick={() => setKeypad(true)} disabled={!connected} className="w-28"><Grid3x3 />Keypad</Button>
            </div>
          )}
          {v.live && v.machine.state === "speaking" && v.transport === "demo" && (
            <Button size="sm" variant="ghost" onClick={v.interrupt}><Hand />Interrupt</Button>
          )}
          {v.transport === "demo" && !v.live && (
            <p className="max-w-md text-center text-meta text-subtle">Demo audio uses your browser&apos;s speech recognition and voice, routed through the bank&apos;s real voice path. Production calls run over LiveKit.</p>
          )}
        </div>
      </div>

      <aside className="flex max-h-[38vh] min-h-0 shrink-0 flex-col border-t border-border bg-background lg:max-h-none lg:w-[380px] lg:border-l lg:border-t-0">
        <LiveTranscript entries={v.transcript} you={audience === "customer"} />
      </aside>

      <Dialog open={keypad} onOpenChange={setKeypad}>
        <DialogContent title="Keypad" description="Enter the one-time password sent to your phone.">
          <Keypad onSubmit={v.sendDigits} onClose={() => setKeypad(false)} />
        </DialogContent>
      </Dialog>

      {v.transport === "livekit" && v.livekit && (
        <LiveKitRoom serverUrl={v.livekit.url} token={v.livekit.token} connect audio video={false}
          onDisconnected={() => v.dispatch({ type: "END" })} onError={() => v.dispatch({ type: "FAIL", error: "The audio connection failed. Nothing was changed on your account." })}>
          <RoomAudioRenderer />
          <LiveKitBridge onEvent={v.dispatch} onTranscript={v.onTranscript} onLevel={v.setLevel} register={v.registerLiveKit} />
        </LiveKitRoom>
      )}
    </div>
  );
}
