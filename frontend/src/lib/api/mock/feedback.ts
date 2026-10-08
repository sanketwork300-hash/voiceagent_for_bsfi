/** MOCK — no feedback endpoint in the backend yet. Replace with POST /conversations/{id}/feedback. */
export interface Feedback { messageId: string; rating: "up" | "down"; comment?: string; kind?: "feedback" | "issue" }
const store: Feedback[] = [];
export async function submitFeedback(f: Feedback): Promise<{ stored: "local" }> {
  store.push(f);
  return { stored: "local" };
}
