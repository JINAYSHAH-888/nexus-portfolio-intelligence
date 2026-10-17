'use client';

import {useEffect, useRef, type CSSProperties} from 'react';
import {onScrollFrame} from '@/lib/scroll-store';

/* Per-column shade strength: [upper band, lower band], as % of --brand-11.
 * Irregular on purpose -- an even rhythm reads as stripes, an uneven one as
 * light falling across the grid. Zeros leave some columns clean so the
 * shaded ones have something to contrast with. */
const SHADES: [number, number][] = [
  [0, 3], [6, 2], [2, 7], [9, 0], [0, 4], [4, 8],
  [11, 3], [3, 0], [0, 6], [7, 2], [2, 9], [5, 0],
];

/**
 * The 12-column grid as hairlines through the whole document, with faded
 * dark-red shades between the lines.
 *
 * It replaces section dividers as the page's structure: continuous lines that
 * unify the page instead of bands that cut it. The shades drift very slowly on
 * their own clock (a compositor transform, no repaint), so the background is
 * alive even when scrolling stops. The leftmost rule fills top-to-bottom in
 * accent as you read.
 */
export default function GridField() {
  const fill = useRef<HTMLElement>(null);

  useEffect(
    () =>
      onScrollFrame((s) => {
        if (fill.current) fill.current.style.transform = `scaleY(${s.progress.toFixed(4)})`;
      }),
    [],
  );

  return (
    <div className="grid-field" aria-hidden="true">
      <div className="grid-page grid-field__cols relative">
        {SHADES.map(([a, b], i) => (
          <span key={i}>
            <i
              className="grid-field__shade"
              style={
                {
                  '--s1': `${a}%`,
                  '--s2': `${b}%`,
                  '--t': `${20 + ((i * 7) % 11)}s`,
                  '--delay': `${-i * 3.7}s`,
                } as CSSProperties
              }
            />
          </span>
        ))}
        <b
          ref={fill}
          className="absolute inset-y-0 left-[var(--grid-gutter)] w-px origin-top bg-accent"
          style={{transform: 'scaleY(0)'}}
        />
      </div>
    </div>
  );
}
