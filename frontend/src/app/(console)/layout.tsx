import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  if (!(await cookies()).get("bfsi_staff")?.value) redirect("/auth/login");
  return <AppShell>{children}</AppShell>;
}
