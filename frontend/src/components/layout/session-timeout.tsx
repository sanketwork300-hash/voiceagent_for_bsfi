"use client";
import { useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useProfile } from "@/hooks/use-profile";
import { useSignOut } from "./topbar";

const IDLE_MS = 15 * 60_000;
const WARN_BEFORE_MS = 2 * 60_000;

/** Warns before the staff session expires (token lifetime) or after 15 minutes of inactivity, then signs out. */
export function SessionTimeout() {
  const { profile } = useProfile();
  const signOut = useSignOut();
  const [warning, setWarning] = useState<null | "idle" | "expiry">(null);
  const [left, setLeft] = useState(0);
  const lastActive = useRef(0);

  useEffect(() => {
    lastActive.current = Date.now();
    const bump = () => { lastActive.current = Date.now(); };
    const evs = ["pointerdown", "keydown", "scroll"] as const;
    evs.forEach((e) => window.addEventListener(e, bump, { passive: true }));
    const onUnauthorized = () => void signOut();
    window.addEventListener("bfsi:unauthorized", onUnauthorized);
    return () => { evs.forEach((e) => window.removeEventListener(e, bump)); window.removeEventListener("bfsi:unauthorized", onUnauthorized); };
  }, [signOut]);

  useEffect(() => {
    if (!profile) return;
    const t = setInterval(() => {
      const now = Date.now();
      const expiryLeft = profile.expires_at * 1000 - now;
      const idleLeft = IDLE_MS + WARN_BEFORE_MS - (now - lastActive.current);
      if (expiryLeft <= 0 || idleLeft <= 0) { clearInterval(t); void signOut(); return; }
      if (expiryLeft < WARN_BEFORE_MS) { setWarning("expiry"); setLeft(expiryLeft); }
      else if (idleLeft < WARN_BEFORE_MS) { setWarning("idle"); setLeft(idleLeft); }
      else setWarning(null);
    }, 1000);
    return () => clearInterval(t);
  }, [profile, signOut]);

  const secs = Math.max(0, Math.round(left / 1000));
  return (
    <ConfirmDialog open={warning !== null} onOpenChange={(o) => { if (!o) { lastActive.current = Date.now(); setWarning(null); } }}
      tone="primary" title={warning === "expiry" ? "Your session is about to end" : "Are you still there?"}
      description={warning === "expiry"
        ? `For security, sessions last a fixed time. You'll be signed out in ${secs}s — sign in again to continue.`
        : `You've been inactive. You'll be signed out in ${secs}s to protect customer data.`}
      confirmLabel={warning === "expiry" ? "Sign in again" : "Stay signed in"}
      onConfirm={() => { if (warning === "expiry") return signOut(); lastActive.current = Date.now(); setWarning(null); }} />
  );
}
