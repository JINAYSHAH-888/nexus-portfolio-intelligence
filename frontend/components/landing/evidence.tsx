'use client';

import {useRef} from 'react';
import {gsap} from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';
import {useGSAP} from '@gsap/react';
import Movement from './movement';
import ScrollText from './scroll-text';
import {facts, shortScheme, shortStock} from '@/lib/landing-series';

if (typeof window !== 'undefined') gsap.registerPlugin(ScrollTrigger, useGSAP);

const pair = facts.topPair;
const trio = facts.trio;

/* Three steps, one sentence and ONE number each. The story is carried by a single
 * giant figure at a time, rounded; nothing else on the page is a number. */
const STEPS = [
  {
    say: `By name, ${shortScheme(pair.a)} and ${shortScheme(pair.b)} look like different funds.`,
    value: pair.sharedCount,
    suffix: '',
    label: 'Holdings in common',
  },
  {
    say: 'By money, they are far more alike.',
    value: Math.round(pair.weightedOverlap),
    suffix: '%',
    label: 'Of their money in the same stocks',
  },
  {
    say: 'Add a third fund, and one bank becomes a tenth of everything.',
    value: Math.round(trio.pct),
    suffix: '%',
    label: `${shortStock(trio.stock)}, across three funds`,
  },
];

/**
 * Movement 02 -- the first pinned beat. The panel holds still while scroll
 * advances the three steps; each figure counts up as its step arrives.
 *
 * Every final value is in the server HTML. The scrub only runs on desktop
 * with motion allowed (the same media query as the `.ev-steps` CSS that
 * stacks the steps), and restores those values on revert. Phones and reduced
 * motion get the three steps in a plain column.
 */
export default function Evidence() {
  const pin = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add('(min-width: 1024px) and (prefers-reduced-motion: no-preference)', () => {
        const root = pin.current;
        if (!root) return;
        const steps = gsap.utils.toArray<HTMLElement>('[data-step]', root);
        const nums = gsap.utils.toArray<HTMLElement>('[data-count]', root);

        gsap.set(steps.slice(1), {opacity: 0, y: 32});
        nums.slice(1).forEach((n) => (n.textContent = '0'));

        const counter = (el: HTMLElement) => {
          const o = {v: 0};
          const target = Number(el.dataset.count);
          return [o, {v: target, duration: 0.5, onUpdate: () => (el.textContent = String(Math.round(o.v)))}] as const;
        };

        const tl = gsap.timeline({
          defaults: {ease: 'none'},
          scrollTrigger: {
            trigger: root,
            start: 'top top',
            end: '+=220%',
            pin: true,
            scrub: 0.8,
            anticipatePin: 1,
            invalidateOnRefresh: true,
          },
        });
        tl.to({}, {duration: 0.4});
        for (let i = 1; i < steps.length; i++) {
          const [o, to] = counter(nums[i]);
          tl.to(steps[i - 1], {opacity: 0, y: -32, duration: 0.3})
            .to(steps[i], {opacity: 1, y: 0, duration: 0.3}, '<0.1')
            .to(o, to, '<')
            .to({}, {duration: 0.45});
        }

        return () => nums.forEach((n) => (n.textContent = n.dataset.count ?? ''));
      });
    },
    {scope: pin},
  );

  return (
    <Movement id="evidence" density="none">
      <div ref={pin} className="relative flex min-h-dvh items-center py-26 lg:py-20">
        <div className="grid-page w-full">
          <div className="col-span-12 lg:col-span-7">
            <p className="label">The evidence</p>
            <ScrollText
              id="evidence-title"
              text="Different names. The same money."
              className="mt-4 max-w-[14ch] font-display text-display-2 font-semibold text-text"
            />
          </div>

          <ol className="ev-steps col-span-12 mt-14 grid gap-14">
            {STEPS.map((s, i) => (
              <li key={i} data-step className="grid grid-cols-12 items-center gap-x-6 gap-y-6">
                <div className="col-span-12 lg:col-span-6">
                  <p className="font-display text-step-5 font-semibold leading-tight text-text">{s.say}</p>
                </div>
                <div className="glass col-span-12 px-8 py-10 lg:col-span-5 lg:col-start-8 lg:text-right">
                  <p className="relative z-[2] font-display text-display font-semibold leading-none tabular-nums text-display-accent">
                    <span data-count={s.value}>{s.value}</span>
                    {s.suffix}
                  </p>
                  <span className="label relative z-[2] mt-4">{s.label}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Movement>
  );
}
