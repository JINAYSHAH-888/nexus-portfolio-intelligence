'use client';

import {useEffect} from 'react';
import {usePathname} from 'next/navigation';
import {applyTheme, onThemeChange, routeDefault, storedTheme} from '@/lib/theme';
import {clearTokenColorCache} from '@/lib/theme-color';

/**
 * Keeps the theme right during CLIENT navigation (the inline head script only
 * runs on a full load): moving from the dark landing into the light app with
 * no explicit choice applies the app's default. An explicit choice is never
 * overridden. Also drops the resolved-token cache whenever the theme flips,
 * so canvas/WebGL colours re-resolve against the new palette.
 */
export default function ThemeController() {
  const pathname = usePathname();

  useEffect(() => {
    if (!storedTheme()) applyTheme(routeDefault(pathname ?? '/'));
  }, [pathname]);

  useEffect(() => onThemeChange(() => clearTokenColorCache()), []);

  return null;
}
