'use client';

import {useEffect, useRef} from 'react';
import {onGemFrame} from '@/lib/gem-store';

/**
 * The red glow the ruby N stands in -- the way the reference's jellyfish
 * glows against black. It follows the gem's projected position every frame
 * and breathes with its opacity, so it is bright where the stone is the
 * subject (opening, close) and fades where the page is dense.
 *
 * Pure transform + opacity on a pre-painted radial gradient: composited, no
 * repaint, no blur filter. Sits BELOW the gem canvas.
 */
export default function GemBloom() {
  const el = useRef<HTMLDivElement>(null);

  useEffect(
    () =>
      onGemFrame((f) => {
        const node = el.current;
        if (!node) return;
        const size = f.r * 4.2;
        node.style.transform = `translate3d(${(f.x - size / 2).toFixed(1)}px, ${(f.y - size / 2).toFixed(1)}px, 0)`;
        node.style.width = node.style.height = `${size.toFixed(0)}px`;
        node.style.opacity = (f.dim * 0.95).toFixed(3);
      }),
    [],
  );

  return (
    <div
      ref={el}
      aria-hidden="true"
      className="gem-bloom pointer-events-none fixed left-0 top-0 z-0 rounded-full"
      style={{opacity: 0}}
    />
  );
}
