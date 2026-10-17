"use client";

import React from "react";
import Link from "next/link";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-paper grid lg:grid-cols-2 text-ink selection:bg-crimson selection:text-paper font-sans">
      
      {/* Left Column: Focused Interactive Form */}
      <div className="flex flex-col justify-center px-6 sm:px-12 md:px-16 py-12 max-w-lg mx-auto w-full">
        <div className="mb-8 select-none">
          {/* Logo */}
          <Link href="/" className="inline-flex items-center gap-2.5">
            <div className="border border-crimson text-crimson px-2.5 py-0.5 text-lg font-mono font-bold tracking-tighter bg-crimson/5">
              N
            </div>
            <span className="font-display text-sm font-semibold tracking-tight uppercase">
              Nexus Portfolio Intelligence
            </span>
          </Link>
        </div>
        
        {children}
      </div>

      {/* Right Column: Decorative Editorial Sidepanel */}
      <div className="hidden lg:flex flex-col justify-between p-16 bg-paper-tint border-l border-line relative overflow-hidden select-none">
        {/* Subtle grid pattern background */}
        <div className="absolute inset-0 opacity-[0.03] pointer-events-none" 
             style={{ 
               backgroundImage: `radial-gradient(circle, var(--color-ink) 1px, transparent 1px)`, 
               backgroundSize: "24px 24px" 
             }} 
        />
        
        {/* Top text */}
        <div className="relative font-mono text-xs uppercase tracking-[0.2em] text-crimson font-semibold">
          analytical validation • system terminal
        </div>

        {/* Centerpiece quote */}
        <div className="relative my-auto space-y-6">
          <p className="font-display text-4xl leading-[1.2] text-ink font-medium tracking-tight">
            &ldquo;The greatest obstacle to financial security is the illusion of diversification.&rdquo;
          </p>
          <div className="h-px w-12 bg-crimson" />
          <p className="font-mono text-xs text-ink-soft uppercase tracking-wider">
            Nippon Portfolio quantitative research
          </p>
        </div>

        {/* Bottom stats / status indicator */}
        <div className="relative flex justify-between items-center text-[10px] font-mono text-ink-soft border-t border-line/40 pt-6">
          <span>PORT 3000 • DEPLOY STANDALONE</span>
          <span className="flex items-center gap-1.5 uppercase font-semibold">
            <span className="w-1.5 h-1.5 rounded-full bg-crimson animate-pulse" />
            LIVE SIMULATION ONLINE
          </span>
        </div>

      </div>

    </div>
  );
}
