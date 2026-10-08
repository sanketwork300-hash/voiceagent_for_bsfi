/**
 * MOCK — features without backend endpoints yet: staff MFA, device sessions, workflows. Each function documents the
 * endpoint it should be replaced with. Kept behind the same apiClient interface so swapping is a one-line change.
 */
export const PLATFORM_MOCK = true as const;

export interface DeviceSession { id: string; device: string; location: string; lastActive: string; current: boolean }

/** Replace with GET /auth/sessions */
export async function listDeviceSessions(): Promise<DeviceSession[]> {
  return [{ id: "current", device: typeof navigator !== "undefined" ? navigator.userAgent.split(")")[0].split("(")[1] ?? "This browser" : "This browser",
    location: "This device", lastActive: new Date().toISOString(), current: true }];
}

export interface Workflow { id: string; name: string; trigger: string; steps: string[]; status: "Active" | "Draft" }

/** Replace with GET /workflows. These describe flows the backend runtime already enforces. */
export async function listWorkflows(): Promise<Workflow[]> {
  return [
    { id: "wf-transfer", name: "Funds transfer", trigger: "transfer_money requested", status: "Active",
      steps: ["Policy check", "Transaction OTP bound to amount and payee", "Maker-checker above ₹5,00,000", "Customer confirmation", "Bank transfer API", "Receipt"] },
    { id: "wf-card-block", name: "Card block", trigger: "block_card requested", status: "Active",
      steps: ["Card selection", "Policy check (relaxed during fraud reports)", "Customer confirmation", "Card service (MCP)", "Reference issued"] },
    { id: "wf-fraud", name: "Fraud report", trigger: "Fraud intent detected", status: "Active",
      steps: ["Flag session", "Offer card block", "Urgent handoff to fraud desk with context"] },
    { id: "wf-otp-lockout", name: "Authentication lockout", trigger: "3 failed OTPs", status: "Active",
      steps: ["Cancel pending action", "Handoff (authentication failure)"] },
  ];
}
