'use client';

import dynamic from 'next/dynamic';
import {useEffect, useState} from 'react';
import {tokenColor} from '@/lib/theme-color';
import {useTheme} from '@/components/theme/use-theme';

const ElectricLogo = dynamic(() => import('@/components/vendor/electric-logo'), {ssr: false});

/**
 * The N mark as live lightning -- React Bits' ElectricLogo tracing the brand
 * N, in dark brand reds, in its "light" theme (crisp electric ink, because
 * the nav is white). Hovering charges it; a click sends a ripple through it.
 *
 * The box is a fixed size before and after the canvas mounts, so the nav
 * never shifts. The mark charges up over ~1.4s, which lands while the
 * entrance curtain is still parting.
 */
export default function ElectricMark({size = 64}: {size?: number}) {
  const theme = useTheme();
  // Re-resolved per theme: the ramps differ, and tokenColor's cache is
  // cleared on every theme change by ThemeController. HEX only -- the
  // component parses colours with hexToRgb.
  const [colors, setColors] = useState<{core: string; glow: string} | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setColors(
      theme === 'dark'
        ? // Emitted light on black: a near-white hot core, crimson glow.
          {core: tokenColor('--brand-12'), glow: tokenColor('--brand-9')}
        : // Ink on off-white: deep crimson strands, vivid glow.
          {core: tokenColor('--brand-11'), glow: tokenColor('--brand-9')},
    );
  }, [theme]);

  const dark = theme === 'dark';
  return (
    // The radial mask fades the canvas to nothing before its square edge.
    <span
      aria-hidden="true"
      className="block shrink-0 [mask-image:radial-gradient(circle,black_55%,transparent_72%)]"
      style={{width: size, height: size}}
    >
      {colors && (
        <ElectricLogo
          src="/n-mark-alpha.svg"
          theme={dark ? 'dark' : 'light'}
          color={colors.core}
          glowColor={colors.glow}
          scale={0.58}
          // Brighter than before in both themes (owner request). In dark it
          // is EMITTED light, so the bloom that read as a pink box on white
          // is exactly what makes it glow on black.
          intensity={dark ? 1.9 : 1.7}
          glow={dark ? 1.15 : 0.1}
          thickness={dark ? 1.5 : 1.35}
          strands={3}
          bend={0.32}
          crackle={0.9}
          arcs={1}
          flicker={0.45}
          fill={dark ? 0.2 : 0}
          speed={2}
          cursorRadius={40}
        />
      )}
    </span>
  );
}
