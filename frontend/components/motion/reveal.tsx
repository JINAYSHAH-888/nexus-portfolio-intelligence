'use client';

import {useRef, type ReactNode} from 'react';
import {useGSAP} from '@gsap/react';
import {gsap} from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';

if (typeof window !== 'undefined') gsap.registerPlugin(ScrollTrigger, useGSAP);

type RevealProps = {
  children: ReactNode;
  /** Stagger children matching this selector instead of the container. */
  stagger?: string;
  /** rows .035 | cards .05 | labels .02 — see DESIGN.md */
  rhythm?: 'rows' | 'cards' | 'labels';
  as?: 'div' | 'section' | 'ul' | 'tbody';
  className?: string;
};

const RHYTHM = {rows: 0.035, cards: 0.05, labels: 0.02} as const;

/**
 * Entry reveal for data: a short rise and fade, staggered on the archetype's
 * rhythm tokens.
 *
 * Deliberately NOT a scrub. In engineered-precision a scrubbed entry means the
 * reader can park a table half-revealed, which makes data unreadable at exactly
 * the moment they are trying to read it. Scrubbing is reserved for the pinned
 * narrative sequence, where partial progress is the point.
 *
 * `once: true` — re-animating content the reader has already seen on the way
 * back up is motion that carries no information, which this archetype forbids.
 *
 * Under reduced motion nothing animates and nothing is hidden: content renders
 * at its final state with no opacity:0 to get stuck on.
 */
export default function Reveal({
  children,
  stagger,
  rhythm = 'cards',
  as: Tag = 'div',
  className = '',
}: RevealProps) {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      const el = root.current;
      if (!el) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const targets = stagger
        ? Array.from(el.querySelectorAll(stagger))
        : [el];
      if (!targets.length) return;

      gsap.fromTo(
        targets,
        {opacity: 0, y: 12},
        {
          opacity: 1,
          y: 0,
          duration: 0.42,
          ease: 'power2.out',
          stagger: RHYTHM[rhythm],
          scrollTrigger: {trigger: el, start: 'top 88%', once: true},
        },
      );
    },
    {scope: root},
  );

  // @ts-expect-error -- polymorphic ref across the allowed tag union
  return <Tag ref={root} className={className}>{children}</Tag>;
}
