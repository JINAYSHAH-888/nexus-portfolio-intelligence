"use client";

import React, { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            // retry: 0 -- a failed query immediately sets isError: true.
            // The default (3 retries with exponential backoff) keeps
            // isLoading: true for many extra seconds after a request has
            // already failed -- exactly what causes "infinite loading" to
            // look infinite. The real resilience mechanism is the 12s
            // AbortController timeout in lib/api-client.ts; retrying an
            // already-timed-out call just multiplies the wait for nothing.
            retry: 0,
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: 0,
          },
        },
      })
  );

  return (
    <MotionConfig reducedMotion="user">
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    </MotionConfig>
  );
}
