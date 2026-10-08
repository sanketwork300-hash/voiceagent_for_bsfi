export type Environment = "development" | "staging" | "production";

export const ENVIRONMENT = (process.env.NEXT_PUBLIC_ENVIRONMENT ?? "development") as Environment;
export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";
export const VOICE_TRANSPORT = (process.env.NEXT_PUBLIC_VOICE_TRANSPORT ?? "demo") as "demo" | "livekit";

export function environmentUrls(): Partial<Record<Environment, string>> {
  try {
    return JSON.parse(process.env.NEXT_PUBLIC_ENVIRONMENT_URLS ?? "{}");
  } catch {
    return {};
  }
}
