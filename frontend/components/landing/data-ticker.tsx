'use client';

import {useEffect, useRef} from 'react';
import {onScrollFrame} from '@/lib/scroll-store';

/** `label` renders outlined (texture), `value` solid accent (emphasis). */
export type TickerItem = {label?: string; value?: string};

/**
 * A band of huge outlined type streaming real figures behind the content.
 *
 * Two channels, like everything that moves on this page:
 *   time      it always drifts, so the band is alive when scrolling stops
 *   velocity  scroll speed is added on top, so a fast scroll surges it and it
 *             eases back down as the shared velocity decays
 *
 * Driven from the scroll bus's frame callback (the one GSAP ticker), with one
 * transform write per frame. The content is duplicated so the loop wraps
 * seamlessly. Decorative restatement of facts shown elsewhere, so it is
 * aria-hidden; under reduced motion it holds still.
 */
export default function DataTicker({
  items,
  direction = 1,
  className = '',
}: {
  items: TickerItem[];
  direction?: 1 | -1;
  className?: string;
}) {
  const track = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = track.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    let half = el.scrollWidth / 2;
    const ro = new ResizeObserver(() => (half = el.scrollWidth / 2));
    ro.observe(el);

    let x = direction === 1 ? 0 : -half;
    let last = performance.now();
    const off = onScrollFrame((s) => {
      const now = performance.now();
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const speed = 34 + Math.min(900, Math.abs(s.velocity) * 14);
      x -= direction * speed * dt;
      if (x <= -half) x += half;
      if (x > 0) x -= half;
      el.style.transform = `translate3d(${x.toFixed(1)}px,0,0)`;
    });
    return () => {
      off();
      ro.disconnect();
    };
  }, [direction]);

  return (
    <div aria-hidden="true" className={`pointer-events-none relative overflow-hidden py-6 select-none ${className}`}>
      <div ref={track} className="flex w-max whitespace-nowrap">
        {[0, 1].map((copy) => (
          <div key={copy} className="flex shrink-0 items-baseline">
            {items.map((it, i) => (
              <span key={i} className="ticker-item">
                {it.label && <span className="ticker-outline">{it.label}</span>}
                {it.value && <span className="ticker-value">{it.value}</span>}
                <span className="ticker-sep" />
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
