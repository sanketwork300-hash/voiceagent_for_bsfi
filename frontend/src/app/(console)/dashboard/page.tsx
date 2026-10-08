import type { Metadata } from "next";
import { Dashboard } from "@/components/monitoring/overview";

export const metadata: Metadata = { title: "Overview" };
export default function Page() { return <Dashboard />; }
