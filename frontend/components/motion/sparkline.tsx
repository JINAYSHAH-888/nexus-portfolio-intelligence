'use client';

import {useId, useMemo, useRef} from 'react';
import {useGSAP} from '@gsap/react';
import {gsap} from 'gsap';
import {ScrollTrigger} from 'gsap/ScrollTrigger';

if (typeof window !== 'undefined') gsap.registerPlugin(ScrollTrigger, useGSAP);

type SparklineProps = {
  data: number[];
  /** Accessible one-line summary. Required — a chart with no text is invisible
   *  to a screen reader, and this archetype shows data, not decoration. */
  summary: string;
  tone?: 'accent' | 'ok' | 'warn' | 'info';
  width?: number;
  height?: number;
  className?: string;
};

/* Step 9 is the ramp's "solid fill" step: tuned for graphic marks rather than
 * glyphs. Text steps (11/12) read too heavy as a 1.5px stroke. */
const STROKE: Record<string, string> = {
  accent: 'var(--brand-9)',
  ok: 'var(--ok-9)',
  warn: 'var(--warn-9)',
  info: 'var(--info-9)',
};

/**
 * An inline sparkline that draws itself on scroll entry.
 *
 * The archetype calls for "inline diagrams and sparklines instead of
 * illustrations" — this is the only visual element on the page that is not
 * type or a rule, and it exists because it carries data.
 *
 * The draw-on effect uses stroke-dashoffset rather than the DrawSVG plugin:
 * identical result for a single open path, no extra plugin in the bundle, and
 * it degrades to a fully-drawn line if JS never runs.
 *
 * Baseline and final point are always visible, so the shape reads before and
 * after the animation rather than only after it.
 */
export default function Sparkline({
  data,
  summary,
  tone = 'accent',
  width = 120,
  height = 32,
  className = '',
}: SparklineProps) {
  const root = useRef<SVGSVGElement>(null);
  const titleId = useId();

  const {d, lastX, lastY} = useMemo(() => {
    if (data.length < 2) return {d: '', lastX: 0, lastY: 0};
    const min = Math.min(...data);
    const max = Math.max(...data);
    const span = max - min || 1;
    const pad = 2;
    const pts = data.map((v, i) => {
      const x = (i / (data.length - 1)) * (width - pad * 2) + pad;
      const y = height - pad - ((v - min) / span) * (height - pad * 2);
      return [x, y] as const;
    });
    return {
      d: pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' '),
      lastX: pts[pts.length - 1][0],
      lastY: pts[pts.length - 1][1],
    };
  }, [data, width, height]);

  useGSAP(
    () => {
      const svg = root.current;
      if (!svg || !d) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const path = svg.querySelector<SVGPathElement>('[data-path]');
      const dot = svg.querySelector<SVGCircleElement>('[data-dot]');
      if (!path) return;

      const len = path.getTotalLength();
      gsap.set(path, {strokeDasharray: len, strokeDashoffset: len});
      if (dot) gsap.set(dot, {opacity: 0});

      const tl = gsap.timeline({
        scrollTrigger: {trigger: svg, start: 'top 92%', once: true},
      });
      tl.to(path, {strokeDashoffset: 0, duration: 0.7, ease: 'power2.inOut'});
      if (dot) tl.to(dot, {opacity: 1, duration: 0.14}, '-=0.1');
    },
    {scope: root, dependencies: [d]},
  );

  if (!d) return null;

  return (
    <svg
      ref={root}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-labelledby={titleId}
      preserveAspectRatio="none"
    >
      <title id={titleId}>{summary}</title>
      <path
        data-path
        d={d}
        fill="none"
        stroke={STROKE[tone]}
        strokeWidth="1.5"
        strokeLinecap="square"
        strokeLinejoin="miter"
        vectorEffect="non-scaling-stroke"
      />
      <circle data-dot cx={lastX} cy={lastY} r="2" fill={STROKE[tone]} />
    </svg>
  );
}
