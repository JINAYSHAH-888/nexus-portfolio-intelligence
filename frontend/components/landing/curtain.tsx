import type {CSSProperties} from 'react';

/**
 * The entrance (the role requested as Skiper UI `skiper8`, a Pro component
 * this project has no licence for -- this is an original build).
 *
 * Crimson panels hold the wordmark, a rule draws across, and the two halves
 * part to reveal the page and the gem behind them. Entirely CSS (see
 * `.curtain` in globals.css), so it plays from first paint with no hydration
 * wait, never intercepts a click, and disappears under reduced motion.
 */
export default function Curtain({asOf}: {asOf: string}) {
  const word = 'NEXUS';
  return (
    <div className="curtain" aria-hidden="true">
      <div className="curtain__half curtain__half--top" />
      <div className="curtain__half curtain__half--bottom" />
      <div className="curtain__mark">
        <div className="curtain__word">
          {Array.from(word).map((c, i) => (
            <span key={i} style={{'--i': i} as CSSProperties}>
              {c}
            </span>
          ))}
        </div>
        <div className="curtain__rule" />
        <span className="label text-current">Portfolio Intelligence · Factsheets {asOf}</span>
      </div>
    </div>
  );
}
