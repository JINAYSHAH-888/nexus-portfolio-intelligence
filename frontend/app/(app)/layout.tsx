"use client";

import React from "react";
import { AppNav } from "@/components/app/app-nav";
import { AuthGuard } from "@/components/app/auth-guard";
import GridField from "@/components/landing/grid-field";

/**
 * Shell for every signed-in screen: the landing page's identity (the grid
 * field with its red shades, the electric N, full width) carried into the
 * product, so moving from the marketing page into the app does not feel
 * like changing websites.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <div className="app-bleed relative flex min-h-dvh flex-col bg-bg font-sans text-text">
        <GridField />
        <AppNav />

        <main id="main" tabIndex={-1} className="relative z-10 flex-1 py-12 outline-none">
          <div className="grid-page">
            <div className="col-span-12 min-w-0">{children}</div>
          </div>
        </main>

        {/* Regulatory disclaimer. The SEBI-mandated risk line is quoted
            verbatim; the rest states independence and the nature of the
            simulations. Required on every screen that shows analysis. */}
        <footer className="relative z-10 mt-20 border-t border-line bg-bg">
          <div className="grid-page py-8">
            <div className="col-span-12 grid gap-3 font-mono text-step--2 leading-relaxed text-text-muted lg:col-span-9">
              <p className="text-text-secondary">
                Mutual fund investments are subject to market risks, read all scheme related documents carefully.
              </p>
              <p className="max-w-[110ch]">
                Nexus Portfolio Intelligence is independent analysis software, not affiliated with, endorsed by, or
                sponsored by Nippon Life India Asset Management Limited or AMFI. Scheme data comes from public
                factsheets and AMFI filings. PLOS and ARISE results are model estimates, not guarantees of future
                returns, and not investment advice.
              </p>
            </div>
          </div>
        </footer>
      </div>
    </AuthGuard>
  );
}
