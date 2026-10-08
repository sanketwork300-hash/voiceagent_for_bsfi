import type { Metadata } from "next";
import { VoiceConsole } from "@/components/voice/voice-console";

export const metadata: Metadata = { title: "Voice" };

export default function VoicePage() {
  return <VoiceConsole audience="operator" chatHref="/chat" />;
}
