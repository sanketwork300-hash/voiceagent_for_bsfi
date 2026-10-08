import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { AuthState, SessionView } from "@/types/domain";

/**
 * The active customer session (shared by chat and voice, so switching channel keeps the conversation).
 * The session token is short-lived and scoped to this one session; it is kept in sessionStorage (tab-scoped)
 * because the WebSocket and LiveKit connections need it. Staff tokens never live here.
 */
interface CustomerSessionState {
  sessionId: string | null;
  token: string | null;
  expiresAt: number | null;
  session: SessionView | null;
  kind: "demo_authenticated" | "anonymous" | null;
  customerId: string | null; // known only for demo sign-ins; otherwise the backend holds it
  start: (s: { session: SessionView; session_token: string; expires_in: number }, kind: "demo_authenticated" | "anonymous", customerId?: string) => void;
  update: (patch: Partial<SessionView>) => void;
  setAuthState: (a: AuthState) => void;
  clear: () => void;
}

export const useCustomerSession = create<CustomerSessionState>()(
  persist(
    (set) => ({
      sessionId: null,
      token: null,
      expiresAt: null,
      session: null,
      kind: null,
      customerId: null,
      start: (s, kind, customerId) => set({ sessionId: s.session.session_id, token: s.session_token, session: s.session, kind,
                                            customerId: customerId ?? null, expiresAt: Date.now() + s.expires_in * 1000 }),
      update: (patch) => set((st) => (st.session ? { session: { ...st.session, ...patch } } : st)),
      setAuthState: (a) => set((st) => (st.session ? { session: { ...st.session, authentication_state: a } } : st)),
      clear: () => set({ sessionId: null, token: null, session: null, expiresAt: null, kind: null, customerId: null }),
    }),
    { name: "bfsi-customer-session", storage: createJSONStorage(() => sessionStorage) },
  ),
);

export function sessionUsable(s: { token: string | null; expiresAt: number | null }): boolean {
  return Boolean(s.token && s.expiresAt && s.expiresAt - Date.now() > 30_000);
}
