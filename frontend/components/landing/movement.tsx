'use client';

import {useEffect, useRef, type ReactNode} from 'react';
import {registerMovement, type MovementId} from '@/lib/scroll-store';

/**
 * One movement of the landing page.
 *
 * Deliberately NOT a "section" in the visual sense: no border, no background
 * tint, nothing that announces a boundary. Rhythm comes from an explicit
 * density (so spacing is a decision, not a repeated class string), and the
 * only boundary mark is a pair of small crosshairs at the grid edges.
 *
 * It registers its root with the scroll bus so the gem's stations can be
 * measured against where this movement REALLY is after pins are applied.
 */
const DENSITY = {
  none: '',
  tight: 'py-14',
  base: 'py-26',
  open: 'py-36',
} as const;

export default function Movement({
  id,
  density = 'base',
  className = '',
  marks = true,
  children,
}: {
  id: MovementId;
  density?: keyof typeof DENSITY;
  className?: string;
  marks?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    registerMovement(id, ref.current);
    return () => registerMovement(id, null);
  }, [id]);

  return (
    <section
      ref={ref}
      id={id}
      data-movement={id}
      aria-labelledby={`${id}-title`}
      className={`relative ${DENSITY[density]} ${className}`}
    >
      {marks && (
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0">
          <div className="relative mx-auto max-w-[var(--grid-max)]">
            <span className="cross -top-[5px] left-[calc(var(--grid-gutter)-5px)]" />
            <span className="cross -top-[5px] right-[calc(var(--grid-gutter)-5px)]" />
          </div>
        </div>
      )}
      {children}
    </section>
  );
}
