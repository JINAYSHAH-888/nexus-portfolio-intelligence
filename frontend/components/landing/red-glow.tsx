'use client';

import dynamic from 'next/dynamic';
import {useEffect, useState} from 'react';
import {tokenColor} from '@/lib/theme-color';
import {useTheme} from '@/components/theme/use-theme';

// WebGL2 fluid sim: kept out of the first-load bundle, like the gem.
const GlowCursor = dynamic(() => import('@/components/vendor/glow-cursor'), {ssr: false});

/**
 * A red ink trail behind the pointer -- React Bits' GlowCursor, in the brand
 * reds, in its light ("ink") theme because this page is white.
 *
 * Placed as a fixed layer UNDER the content and under the gem, so trails flow
 * behind text and cards and never smear across what you are reading.
 *
 * Only mounted for a fine pointer with motion allowed. On touch it would
 * paint wherever a thumb scrolls, and its fluid solve (20 pressure passes a
 * frame) is battery a phone should not spend on that. It sleeps on its own
 * once the pointer is still, so it costs nothing at rest.
 */
export default function RedGlow() {
  const theme = useTheme();
  const [colors, setColors] = useState<{main: string; deep: string} | null>(null);

  useEffect(() => {
    const ok =
      window.matchMedia('(pointer: fine)').matches &&
      !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!ok) return;
    // Tokens resolve only in the browser, and differ per theme.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setColors({main: tokenColor('--brand-9'), deep: tokenColor('--brand-11')});
  }, [theme]);

  if (!colors) return null;
  const dark = theme === 'dark';

  return (
    <GlowCursor
      className="glow-layer"
      color={colors.main}
      secondaryColor={colors.deep}
      // Dark: emitted red light, screen-blended onto black. Light: red ink.
      theme={dark ? 'dark' : 'light'}
      intensity={dark ? 1.25 : 1.15}
      trailWidth={3}
      linger={1.3}
      glow={dark ? 1.3 : 1}
      hotspot={dark ? 0.7 : 0.6}
      followSpeed={0.6}
      clickBurst
      quality="medium"
      maxDevicePixelRatio={1.5}
    />
  );
}
