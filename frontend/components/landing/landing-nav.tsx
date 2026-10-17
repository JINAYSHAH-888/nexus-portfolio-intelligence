'use client';

import Link from 'next/link';
import {useEffect, useState} from 'react';
import ElectricMark from '@/components/brand/electric-mark';
import ThemeToggle from '@/components/theme/theme-toggle';
import {onScrollFrame, onSpans, type MovementId, type MovementSpan} from '@/lib/scroll-store';

const SECTIONS: {id: MovementId; label: string}[] = [
  {id: 'evidence', label: 'Evidence'},
  {id: 'engines', label: 'Engines'},
  {id: 'ledger', label: 'Ledger'},
];

/**
 * Floating liquid-glass navigation (owner reference: AgentAI / Apple liquid
 * glass). The electric N, a glass pill of section links whose current item
 * is a lit crimson pill, and the theme switch plus account actions.
 *
 * The active section is read from the shared scroll bus -- a guarded state
 * update only when the section actually changes, never per frame.
 */
export default function LandingNav() {
  const [active, setActive] = useState<MovementId | null>(null);

  useEffect(() => {
    let spans: MovementSpan[] = [];
    const offSpans = onSpans((s) => (spans = s));
    const offFrame = onScrollFrame((s) => {
      let cur: MovementId | null = null;
      for (const sp of spans) if (s.progress >= sp.start - 0.01) cur = sp.id;
      const next = SECTIONS.some((x) => x.id === cur) ? cur : null;
      setActive((prev) => (prev === next ? prev : next));
    });
    return () => {
      offSpans();
      offFrame();
    };
  }, []);

  return (
    <header className="fixed inset-x-0 top-0 z-30 select-none pt-3">
      <nav aria-label="Primary" className="grid-page">
        <div className="col-span-12 flex items-center justify-between gap-3">
          <Link href="/" className="-ml-2 flex items-center gap-1" aria-label="Nexus Portfolio Intelligence — home">
            <ElectricMark size={60} />
            <span className="font-display text-step-1 font-semibold tracking-tight text-text">Nexus</span>
          </Link>

          <ul className="glass-pill hidden items-center gap-1 p-1.5 md:flex">
            {SECTIONS.map((s) => {
              const on = active === s.id;
              return (
                <li key={s.id} className="relative z-[2]">
                  <a
                    href={`#${s.id}`}
                    aria-current={on ? 'true' : undefined}
                    className={`flex h-9 items-center rounded-full px-4 font-mono text-step--2 uppercase tracking-wider transition-colors duration-200 ${
                      on
                        ? 'bg-accent text-on-accent shadow-[0_6px_20px_-6px_var(--accent)]'
                        : 'text-text-secondary hover:text-text'
                    }`}
                  >
                    {s.label}
                  </a>
                </li>
              );
            })}
          </ul>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link href="/login" className="btn-glass glass-pill hidden sm:inline-flex">
              <span className="relative z-[2]">Sign in</span>
            </Link>
            <Link href="/register" className="btn-ember">
              Create profile
            </Link>
          </div>
        </div>
      </nav>
    </header>
  );
}
