'use client';

import {useRef, type ElementType} from 'react';
import {motion, useReducedMotion, useScroll, useTransform, type MotionValue} from 'motion/react';

/**
 * Scroll-converging text.
 *
 * Adapted from Skiper UI `skiper31` (CharacterV1), credited in the footer as
 * its free licence requires. Each character starts fanned out from the centre
 * of the line, tilted in 3D, and converges into place as the heading scrolls
 * up the viewport. Scroll owns it completely: park mid-way and it holds there.
 *
 * Changes from the original, and why:
 *  - `framer-motion` -> `motion/react`. This project is on motion v12, the
 *    renamed successor; installing both would ship two animation libraries.
 *  - no `<ReactLenis root>`. Lenis already runs once, app-wide, on the GSAP
 *    ticker. A second instance would fight it for the same scroll.
 *  - characters are grouped into nowrap WORD spans. Bare inline-block letters
 *    let the browser break a line in the middle of a word.
 *  - the real sentence is rendered once for assistive tech and the letter
 *    spans are aria-hidden. Split letters are otherwise read one at a time.
 *  - the fan width scales with line length, so a long heading does not throw
 *    its outer letters a thousand pixels off-screen.
 *  - reduced motion renders plain text: no fan, no tilt, nothing to wait for.
 */
type Props = {
  text: string;
  as?: 'h1' | 'h2' | 'h3' | 'p';
  id?: string;
  className?: string;
  /** Override the per-character fan distance in px. */
  spread?: number;
};

export default function ScrollText({text, as = 'h2', id, className = '', spread}: Props) {
  const ref = useRef<HTMLElement>(null);
  const reduce = useReducedMotion();
  const {scrollYProgress} = useScroll({
    target: ref,
    // Fully converged once the heading's top reaches 40% down the viewport,
    // i.e. comfortably before the reader's eye arrives at it.
    offset: ['start 96%', 'start 40%'],
  });

  const Tag = as as ElementType;
  const letters = text.replace(/\s+/g, '').length;
  const centre = (letters - 1) / 2;
  const step = spread ?? Math.min(26, 760 / Math.max(10, letters));
  const words = text.split(/\s+/);
  let n = 0;

  return (
    <Tag ref={ref} id={id} className={className}>
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        {words.map((word, wi) => (
          <span key={wi}>
            <span className="inline-block whitespace-nowrap" style={{perspective: '600px'}}>
              {Array.from(word).map((ch, ci) => {
                const i = n++;
                return reduce ? (
                  <span key={ci}>{ch}</span>
                ) : (
                  <Letter key={ci} ch={ch} offset={i - centre} step={step} progress={scrollYProgress} />
                );
              })}
            </span>
            {wi < words.length - 1 ? ' ' : null}
          </span>
        ))}
      </span>
    </Tag>
  );
}

function Letter({
  ch,
  offset,
  step,
  progress,
}: {
  ch: string;
  offset: number;
  step: number;
  progress: MotionValue<number>;
}) {
  const x = useTransform(progress, [0, 1], [offset * step, 0]);
  const rotateX = useTransform(progress, [0, 1], [Math.max(-80, Math.min(80, offset * 9)), 0]);
  // Never fully transparent: if script fails, the heading is scattered but
  // still legible rather than missing.
  const opacity = useTransform(progress, [0, 0.6], [0.22, 1]);
  return (
    <motion.span className="inline-block" style={{x, rotateX, opacity}}>
      {ch}
    </motion.span>
  );
}
