import type { Metadata } from "next";
import { EvaluationView } from "@/components/monitoring/evaluation";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Evaluation" };

export default function Page() {
  return <RequirePermission perm="evaluation.run"><EvaluationView /></RequirePermission>;
}
