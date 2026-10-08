"use client";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { conversationReducer, framesFromResponse, initialConversation } from "@/lib/chat/conversation";
import { ChatSocket, chatSocketUrl, type SocketStatus } from "@/lib/websocket/chat-socket";
import { useCustomerSession } from "@/store/customer-session";
import type { BackendFrame } from "@/types/events";

const now = () => new Date().toISOString();
let counter = 0;
const localId = () => `u-${Date.now().toString(36)}-${counter++}`;

/**
 * One conversation, streamed over WebSocket (REST fallback). Chat and voice share the backend session, so this
 * hook can be mounted after a voice call and continues the same conversation history.
 */
export function useConversation() {
  const { sessionId, token, session, setAuthState, update } = useCustomerSession();
  const [state, dispatch] = useReducer(conversationReducer, initialConversation(session?.authentication_state));
  const [socketStatus, setSocketStatus] = useState<SocketStatus>("idle");
  const socket = useRef<ChatSocket | null>(null);

  const onFrame = useCallback((frame: BackendFrame) => {
    dispatch({ type: "frame", frame, at: now() });
    if (frame.type === "message.completed") {
      setAuthState(frame.response.authentication_state);
      update({ status: frame.response.handoff ? "HANDED_OFF" : "ACTIVE", handoff_id: frame.response.handoff_id,
               pending_action: frame.response.pending_action, language: frame.response.language });
    }
    if (frame.type === "handoff.resolved") update({ status: "ACTIVE", handoff_id: null });
  }, [setAuthState, update]);

  // hydrate the existing transcript (e.g. returning from a voice call), then stream
  useEffect(() => {
    if (!sessionId || !token) return;
    let cancelled = false;
    dispatch({ type: "reset", authState: session?.authentication_state });
    apiClient.sessions.messages(sessionId, token).then((msgs) => { if (!cancelled) dispatch({ type: "history", messages: msgs }); }).catch(() => undefined);
    const s = new ChatSocket({ url: chatSocketUrl(sessionId, token), onFrame, onStatus: setSocketStatus });
    socket.current = s;
    s.connect();
    return () => { cancelled = true; s.close(); socket.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, token]);

  const sendViaRest = useCallback(async (text: string) => {
    if (!sessionId || !token) return;
    try {
      const r = await apiClient.chat.send(sessionId, token, text);
      for (const f of framesFromResponse(r)) onFrame(f);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      dispatch({ type: "local.error", message: err?.isUnavailable
        ? "The banking service is temporarily unavailable. Your account has not been modified."
        : err?.userMessage ?? "We couldn't send that. Nothing was changed.", reference: err?.reference ?? null });
    }
  }, [sessionId, token, onFrame]);

  const send = useCallback((text: string, opts: { display?: string; masked?: boolean } = {}) => {
    const clean = text.trim();
    if (!clean) return;
    dispatch({ type: "user.sent", id: localId(), text: opts.display ?? clean, at: now(), masked: opts.masked });
    if (socket.current?.isOpen) socket.current.send({ type: "message", content: clean });
    else void sendViaRest(clean);
  }, [sendViaRest]);

  /** OTP goes in its own frame type; it is never echoed into the visible transcript. */
  const sendOtp = useCallback((otp: string) => {
    dispatch({ type: "user.sent", id: localId(), text: "One-time password entered", at: now(), masked: true });
    if (socket.current?.isOpen) socket.current.send({ type: "auth.otp", otp });
    else void sendViaRest(otp);
  }, [sendViaRest]);

  const interrupt = useCallback(() => socket.current?.send({ type: "interrupt" }), []);

  return {
    state, socketStatus, send, sendOtp, interrupt,
    confirm: () => send("yes", { display: "Confirm" }),
    cancel: () => send("no", { display: "Cancel" }),
    ready: Boolean(sessionId && token),
  };
}
