'use client';

import {useRef} from 'react';
import {gsap} from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';
import {useGSAP} from '@gsap/react';
import Movement from './movement';
import ScrollText from './scroll-text';

if (typeof window !== 'undefined') gsap.registerPlugin(ScrollTrigger, useGSAP);

/* Each row is a real entry in the engines' own ledger (arise_engine.py's
 * conservatism ledger, risk_engine.py's backtest). One plain phrase each. */
const ROWS = [
  {item: 'Return haircut', say: 'A safety margin, deducted every year.'},
  {item: 'Shrinkage', say: 'No lucky decade taken at face value.'},
  {item: 'Lifespan', say: 'A mortality curve, not a fixed age.'},
  {item: 'Fat tails', say: 'Crashes as often as they really happen.'},
  {item: 'Backtest', say: 'Pass or fail, the verdict is published.'},
];

/**
 * Movement 04 -- the second and last pinned beat. The ledger writes itself
 * line by line while the page holds still. Two pins is the page's budget: a
 * third would turn the scroll into a slideshow.
 */
export default function Ledger() {
  const pin = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add('(min-width: 1024px) and (prefers-reduced-motion: no-preference)', () => {
        const root = pin.current;
        if (!root) return;
        const rows = gsap.utils.toArray<HTMLElement>('[data-row]', root);
        const rules = gsap.utils.toArray<HTMLElement>('[data-rule]', root);
        const cells = rows.map((r) => r.querySelectorAll('[data-cell]'));

        gsap.set(rules, {scaleX: 0});
        cells.forEach((c) => gsap.set(c, {opacity: 0.12, y: 10}));

        const tl = gsap.timeline({
          defaults: {ease: 'none'},
          scrollTrigger: {
            trigger: root,
            start: 'top top',
            end: '+=140%',
            pin: true,
            scrub: 0.8,
            anticipatePin: 1,
            invalidateOnRefresh: true,
          },
        });
        tl.to({}, {duration: 0.25});
        rows.forEach((_, i) => {
          tl.to(rules[i], {scaleX: 1, duration: 0.3, ease: 'power2.inOut'});
          tl.to(cells[i], {opacity: 1, y: 0, duration: 0.22, stagger: 0.06}, '<0.12');
        });
        tl.to({}, {duration: 0.35});
      });
    },
    {scope: pin},
  );

  return (
    <Movement id="ledger" density="none">
      <div ref={pin} className="relative flex min-h-dvh items-center py-26 lg:py-20">
        <div className="grid-page w-full">
          <div className="col-span-12 lg:col-span-7 lg:col-start-6">
            <p className="label">The ledger</p>
            <ScrollText
              id="ledger-title"
              text="Every cautious assumption, written down."
              className="mt-4 max-w-[16ch] font-display text-display-2 font-semibold text-text"
            />
            <ol className="glass mt-12 px-6 py-2 sm:px-8">
              {ROWS.map((r) => (
                <li key={r.item} data-row className="group/row relative z-[2] grid grid-cols-12 items-baseline gap-x-6 py-5">
                  {/* Rules BETWEEN rows only: the panel's own rim is the top edge. */}
                  <span data-rule aria-hidden="true" className="absolute inset-x-0 top-0 h-px origin-left bg-line group-first/row:hidden" />
                  <h3 data-cell className="col-span-12 font-display text-step-2 font-semibold text-text sm:col-span-5">
                    {r.item}
                  </h3>
                  <p data-cell className="col-span-12 text-step-0 text-text-secondary sm:col-span-7">
                    {r.say}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </Movement>
  );
}
