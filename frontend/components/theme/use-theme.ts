'use client';

import {useSyncExternalStore} from 'react';
import {currentTheme, onThemeChange, type Theme} from '@/lib/theme';

/** The live theme, re-rendering subscribers when it changes. SSR reads light;
 *  the real value arrives on hydration (the attribute is already set). */
export function useTheme(): Theme {
  return useSyncExternalStore(
    (notify) => onThemeChange(() => notify()),
    currentTheme,
    () => 'light',
  );
}
