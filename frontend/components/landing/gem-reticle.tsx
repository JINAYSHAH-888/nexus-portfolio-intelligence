'use client';

import {useEffect, useRef} from 'react';
import {onGemFrame} from '@/lib/gem-store';

/**
 * Crosshair brackets that track the ruby N, with a live readout.
 *
 * The instrument-panel idiom applied to the page's one object: the brackets
 * hold the gem's projected bounds and the readout states its current yaw and
 * refractive index. It fades with the gem's own opacity, so it only appears
 * where the stone is the subject (the opening and the close) and steps back
 * wherever the page is dense.
 *
 * Driven by the scene's frame callback; one transform and two text writes per
 * frame, text only when the rounded value changes. Hidden below 768px, where
 * the gem sits behind the copy and a reticle would frame the text instead.
 */
export default function GemReticle() {
  const root = useRef<HTMLDivElement>(null);
  const yaw = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    let lastDeg = -1;
    return onGemFrame((f) => {
      const el = root.current;
      if (!el) return;
      const o = Math.min(1, Math.max(0, (f.dim - 0.72) / 0.22));
      el.style.opacity = o.toFixed(3);
      if (o === 0) return;
      const w = f.r * 1.55;
      const h = f.r * 2.25;
      el.style.transform = `translate3d(${(f.x - w / 2).toFixed(1)}px, ${(f.y - h / 2).toFixed(1)}px, 0)`;
      el.style.width = `${w.toFixed(0)}px`;
      el.style.height = `${h.toFixed(0)}px`;
      // Wrap AFTER rounding, or 359.6 rounds up to a reading of "360".
      const deg = ((Math.round((f.yaw * 180) / Math.PI) % 360) + 360) % 360;
      if (deg !== lastDeg && yaw.current) {
        lastDeg = deg;
        yaw.current.textContent = String(deg).padStart(3, '0');
      }
    });
  }, []);

  return (
    <div
      ref={root}
      aria-hidden="true"
      className="pointer-events-none fixed left-0 top-0 z-0 hidden md:block"
      style={{opacity: 0}}
    >
      <span className="reticle-corner left-0 top-0 border-l border-t" />
      <span className="reticle-corner right-0 top-0 border-r border-t" />
      <span className="reticle-corner bottom-0 left-0 border-b border-l" />
      <span className="reticle-corner bottom-0 right-0 border-b border-r" />
      <p className="absolute -bottom-7 right-0 whitespace-nowrap font-mono text-step--2 uppercase tracking-wider text-text-muted">
        N-01 · Ruby · IOR 2.42 · Yaw <span ref={yaw} className="tabular-nums text-accent-text">000</span>°
      </p>
      <p className="absolute -top-6 left-0 whitespace-nowrap font-mono text-step--2 uppercase tracking-wider text-text-muted">
        <span className="text-accent-text">●</span> Live render
      </p>
    </div>
  );
}
