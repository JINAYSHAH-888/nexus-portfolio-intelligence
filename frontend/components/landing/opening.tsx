import Link from 'next/link';
import type {CSSProperties} from 'react';
import Movement from './movement';
import {facts, shortScheme, shortStock} from '@/lib/landing-series';

const d = (s: number) => ({'--d': `${s}s`}) as CSSProperties;

/**
 * Movement 01. One statement, one line of support, two actions, three glass
 * facts -- and the gem, glowing in its bloom on the right.
 *
 * Every figure is read from lib/generated/landing-facts.json (re-derived from
 * factsheet holdings and verified against the engine at build time).
 */
export default function Opening() {
  const allFour = facts.holdAllFour.length;
  const total = facts.equitySchemes;
  const top = facts.topPair;

  const cards = [
    {value: `${facts.mostHeld[0].count}/${total}`, label: `Funds holding ${shortStock(facts.mostHeld[0].stock)}`},
    {value: `${allFour}/${total}`, label: 'Hold all four banks'},
    {value: `${Math.round(top.weightedOverlap)}%`, label: `Same money · ${shortScheme(top.a)} × ${shortScheme(top.b)}`},
  ];

  return (
    <Movement
      id="opening"
      density="none"
      marks={false}
      className="relative flex min-h-dvh items-end overflow-x-clip pb-20 pt-[44dvh] lg:items-center lg:pb-16 lg:pt-28"
    >
      {/* The blurred red echo ("Night has come" reference): atmosphere behind
          the type, decorative, so hidden from assistive tech. */}
      <span
        aria-hidden="true"
        className="echo-word pointer-events-none absolute -bottom-6 left-[-2vw] select-none font-display font-bold uppercase leading-none"
      >
        Overlap
      </span>

      <div className="grid-page relative w-full">
        <div className="col-span-12 lg:col-span-7">
          <p className="label rise-fade" style={d(0)}>
            Mutual fund overlap · from the factsheets
          </p>
          <h1 id="opening-title" className="mt-6 max-w-[12ch] font-display text-display font-semibold text-text">
            <span className="rise-mask">
              <span className="rise block" style={d(0)}>
                {allFour === 8 ? 'Eight' : allFour} funds.
              </span>
            </span>
            <span className="rise-mask">
              <span className="rise block" style={d(0.09)}>
                The same <span className="text-display-accent">four banks.</span>
              </span>
            </span>
          </h1>
          <p className="rise-fade mt-8 max-w-[42ch] text-step-1 leading-relaxed text-text-secondary" style={d(0.2)}>
            {/* "each": every one of the four is in 11 of 20 funds, but only 8
                hold all four at once, so "most hold all four" would be false. */}
            HDFC, ICICI, Axis and SBI each sit in more than half of Nippon India&apos;s equity funds.
            See exactly where yours overlap.
          </p>
          <div className="rise-fade mt-10 flex flex-wrap gap-3" style={d(0.3)}>
            <a href="#evidence" className="btn-ember">
              See the overlap
            </a>
            <Link href="/register" className="btn-glass glass-pill">
              <span className="relative z-[2]">Create free profile</span>
            </Link>
          </div>

          <ul className="rise-fade mt-14 grid max-w-2xl grid-cols-3 gap-3" style={d(0.42)}>
            {cards.map((c) => (
              <li key={c.label} className="glass p-4 sm:p-5">
                <p className="relative z-[2] font-display text-step-4 font-semibold leading-none tabular-nums text-text">
                  {c.value}
                </p>
                <p className="relative z-[2] mt-2 text-step--2 leading-snug text-text-secondary">{c.label}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Movement>
  );
}
