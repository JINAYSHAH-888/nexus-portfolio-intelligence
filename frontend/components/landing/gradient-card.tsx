'use client';

import {useRef, type ReactNode} from 'react';

/**
 * Pointer-tracked gradient card (the role requested as Skiper UI `skiper90`,
 * which is a Pro component this project has no licence for -- so this is an
 * original build to the same brief, styled by `.gcard` in globals.css).
 *
 * The only JS is two custom-property writes per pointer move. The light, the
 * border ignition and the fade are all CSS, composited by the browser, with no
 * animation loop. Touch input is ignored: a light that jumps to wherever a
 * finger lands reads as a glitch, not a highlight.
 */
export default function GradientCard({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  return (
    <div
      ref={ref}
      className={`gcard ${className}`}
      onPointerMove={(e) => {
        if (e.pointerType === 'touch') return;
        const el = ref.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        el.style.setProperty('--mx', `${e.clientX - r.left}px`);
        el.style.setProperty('--my', `${e.clientY - r.top}px`);
      }}
    >
      {children}
    </div>
  );
}
