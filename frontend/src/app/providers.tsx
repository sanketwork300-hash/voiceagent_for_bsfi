"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/lib/api/errors";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => !(err instanceof ApiError && [400, 401, 403, 404, 422].includes(err.status)) && count < 2,
      },
    },
  }));
  return <QueryClientProvider client={client}><TooltipProvider>{children}</TooltipProvider></QueryClientProvider>;
}
