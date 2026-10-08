import type { Metadata } from "next";
import { VoiceConsole } from "@/components/voice/voice-console";

export const metadata: Metadata = { title: "Call" };

export default function AssistVoice() {
  return <VoiceConsole audience="customer" chatHref="/assist" />;
}
