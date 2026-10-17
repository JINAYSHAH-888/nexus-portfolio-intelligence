'use client';

import {useRef} from 'react';
import {useGSAP} from '@gsap/react';
import {gsap} from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';

if (typeof window !== 'undefined') gsap.registerPlugin(ScrollTrigger, useGSAP);

export type FigureProps = {
  label: string;
  value: number;
  /** Rendered after the number — '%', 'x', ' yrs'. */
  suffix?: string;
  prefix?: string;
  decimals?: number;
  /** Indian grouping (1,23,456) rather than 123,456. */
  grouping?: 'en-IN' | 'none';
  tone?: 'default' | 'ok' | 'warn' | 'err' | 'info';
  /** Small print under the rule — a provenance note, a caveat, a benchmark name. */
  note?: string;
  className?: string;
};

const TONE: Record<string, string> = {
  default: 'text-text',
  ok: 'text-ok',
  warn: 'text-warn',
  err: 'text-err',
  info: 'text-info',
};

/**
 * THE SIGNATURE MOVE (DESIGN.md → Motion).
 *
 * On scroll entry: the mono label draws in, the figure counts up to its real
 * value, and the 1px rule beneath wipes left-to-right as the number settles.
 *
 * Why it is built this way rather than the obvious way:
 *
 * 1. TABULAR LOCK. The number is rendered in IBM Plex Mono with
 *    `tabular-nums`, and the element reserves its final width via `ch` units
 *    before counting starts. A figure that reflows while animating is the
 *    single most common way a "premium" counter actually looks cheap, and in a
 *    table of returns it shifts every column beside it.
 *
 * 2. THE VALUE IS IN THE DOM FIRST. The final number is the server-rendered
 *    text content; GSAP only overwrites it during the tween. If JS never runs,
 *    the real figure is already on screen and correct.
 *
 * 3. REDUCED MOTION. The counter does not run at all — the value is simply
 *    there. Counting is decoration layered on information; the information
 *    survives on its own.
 */
export default function Figure({
  label,
  value,
  suffix = '',
  prefix = '',
  decimals = 2,
  grouping = 'none',
  tone = 'default',
  note,
  className = '',
}: FigureProps) {
  const root = useRef<HTMLDivElement>(null);

  const format = (n: number) =>
    grouping === 'en-IN'
      ? n.toLocaleString('en-IN', {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        })
      : n.toFixed(decimals);

  const finalText = `${prefix}${format(value)}${suffix}`;

  useGSAP(
    () => {
      const el = root.current;
      if (!el) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const numEl = el.querySelector<HTMLElement>('[data-num]');
      const labelEl = el.querySelector<HTMLElement>('[data-label]');
      const ruleEl = el.querySelector<HTMLElement>('[data-rule]');
      if (!numEl) return;

      const counter = {v: 0};

      const tl = gsap.timeline({
        scrollTrigger: {trigger: el, start: 'top 85%', once: true},
        defaults: {ease: 'power2.out'},
      });

      if (labelEl) {
        tl.fromTo(
          labelEl,
          {opacity: 0, y: 4},
          {opacity: 1, y: 0, duration: 0.14},
        );
      }

      tl.to(
        counter,
        {
          v: value,
          duration: 0.9,
          ease: 'power2.out',
          onUpdate: () => {
            numEl.textContent = `${prefix}${format(counter.v)}${suffix}`;
          },
          // Guarantee the exact final value even if the tween is interrupted.
          onComplete: () => {
            numEl.textContent = finalText;
          },
        },
        '<0.05',
      );

      if (ruleEl) {
        tl.fromTo(
          ruleEl,
          {scaleX: 0},
          {scaleX: 1, duration: 0.55, ease: 'power3.inOut'},
          '<',
        );
      }
    },
    {scope: root, dependencies: [value]},
  );

  return (
    <div ref={root} className={className}>
      <span data-label className="label">
        {label}
      </span>
      <div
        data-num
        data-numeric
        className={`mt-1 block text-step-4 leading-[1.1] font-medium ${TONE[tone]}`}
        style={{
          // Reserve the final width so counting never reflows the row.
          minWidth: `${finalText.length}ch`,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {finalText}
      </div>
      <div
        data-rule
        aria-hidden="true"
        className="mt-2 h-px w-full origin-left bg-line-strong"
      />
      {note ? (
        <p className="mt-2 max-w-[42ch] text-step--1 leading-snug text-text-muted">
          {note}
        </p>
      ) : null}
    </div>
  );
}
