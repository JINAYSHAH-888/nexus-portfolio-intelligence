import React from "react";
import { isDemoMode } from "@/lib/api-client";

/* ──────────────────────────────────────────────────────────────────────────
 * Shared app chrome, rebuilt to the engineered-precision contract in
 * DESIGN.md. These three components appear on every authenticated screen, so
 * the archetype is enforced here rather than re-applied page by page.
 *
 * The rules being enforced:
 *   - every figure carries a small uppercase mono label ABOVE it
 *   - every figure is mono + tabular, so counting or updating never reflows
 *   - containers carry 6px corner ticks drawn with borders, not SVG assets
 *   - no lift, no scale, no shadow: state is expressed with value and border
 * ────────────────────────────────────────────────────────────────────────── */

interface PageHeaderProps {
  eyebrow: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
  /** @deprecated Section numbering was removed product-wide; ignored. */
  index?: string;
}

/**
 * Screen header in the landing page's voice: a mono eyebrow, a display
 * title, one line of support. No index number and no dividing rule -- the
 * landing removed both, and the app should not reintroduce them.
 */
export function PageHeader({ eyebrow, title, description, action }: PageHeaderProps) {
  return (
    <header className="enter mb-12 flex flex-wrap items-end justify-between gap-6">
      <div className="min-w-0">
        <p className="label text-accent-text">{eyebrow}</p>
        <h1 className="mt-3 max-w-[22ch] font-display text-display-2 font-semibold text-text">{title}</h1>
        {description && (
          <p className="mt-4 max-w-[62ch] text-step-1 leading-relaxed text-text-secondary">{description}</p>
        )}
      </div>
      {action ?? <DemoBadge />}
    </header>
  );
}

/**
 * THE FIX for a real bug: this used to render unconditionally, on every
 * page, regardless of whether a real backend was actually connected --
 * meaning it would say "Sample data — backend not connected" even when
 * looking at real, live, backend-computed numbers. Now genuinely reflects
 * lib/api-client.ts's isDemoMode() switch.
 */
export function DemoBadge() {
  if (!isDemoMode()) return null;
  return (
    <span className="inline-flex items-center gap-1.5 border border-accent/40 bg-err-bg px-2.5 py-1 font-mono text-step--2 font-semibold uppercase tracking-wider text-accent-text">
      <span className="h-1.5 w-1.5 rounded-[1px] bg-accent motion-safe:animate-pulse" />
      Sample data — backend not connected
    </span>
  );
}

export type Tone = "default" | "ok" | "warn" | "err" | "info";

const TONE_TEXT: Record<Tone, string> = {
  default: "text-text",
  ok: "text-ok",
  warn: "text-warn",
  err: "text-err",
  info: "text-info",
};

interface StatCardProps {
  label: string;
  value: React.ReactNode;
  /** Provenance, caveat or benchmark name. The archetype's voice: say what
   *  was measured, and say what was not. */
  note?: string;
  tone?: Tone;
  /** Rendered to the right of the value — a sparkline, a delta, a unit. */
  aside?: React.ReactNode;
}

export function StatCard({ label, value, note, tone = "default", aside }: StatCardProps) {
  return (
    <div className="ticked select-none border border-line bg-bg p-5">
      <span className="label">{label}</span>
      <div className="mt-2 flex items-baseline justify-between gap-3">
        <span
          data-numeric
          className={`text-step-3 font-medium leading-none ${TONE_TEXT[tone]}`}
        >
          {value}
        </span>
        {aside}
      </div>
      {note && (
        <p className="mt-3 border-t border-line pt-2 text-step--1 leading-snug text-text-muted">
          {note}
        </p>
      )}
    </div>
  );
}

/**
 * A verdict chip for the states this backend actually returns:
 * Kupiec pass/fail, India SWR band, benchmark_quality, activeness.verdict.
 *
 * Colour is never the sole carrier — each tone ships a glyph and the label
 * text itself, so the meaning survives greyscale, colour-blindness and a
 * screen reader.
 */
const TONE_CHIP: Record<Tone, { cls: string; glyph: string }> = {
  default: { cls: "border-line bg-bg-component text-text-secondary", glyph: "–" },
  ok: { cls: "border-ok/40 bg-ok-bg text-ok", glyph: "✓" },
  warn: { cls: "border-warn/40 bg-warn-bg text-warn", glyph: "!" },
  err: { cls: "border-accent/40 bg-err-bg text-err", glyph: "×" },
  info: { cls: "border-info/40 bg-info-bg text-info", glyph: "?" },
};

export function StatusChip({
  tone = "default",
  children,
}: {
  tone?: Tone;
  children: React.ReactNode;
}) {
  const t = TONE_CHIP[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 border px-2 py-0.5 font-mono text-step--2 uppercase tracking-wider ${t.cls}`}
    >
      <span aria-hidden="true">{t.glyph}</span>
      {children}
    </span>
  );
}

/**
 * Wrapper for a data table: corner ticks, a mono caption, and the horizontal
 * scroll container a dense table needs on a phone without the PAGE scrolling
 * sideways.
 */
export function DataPanel({
  label,
  children,
  note,
}: {
  label: string;
  children: React.ReactNode;
  note?: string;
}) {
  return (
    <section className="ticked border border-line bg-bg">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-5 py-3">
        <h2 className="label">{label}</h2>
        {note && <span className="font-mono text-step--2 text-text-muted">{note}</span>}
      </div>
      <div className="overflow-x-auto">{children}</div>
    </section>
  );
}
