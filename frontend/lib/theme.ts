/**
 * Light / dark theme.
 *
 * Rule: an explicit choice (the toggle) wins everywhere and persists. With no
 * choice made, each surface has its own default -- the landing page is dark
 * (black, crimson, off-white), the app screens are light (off-white, a little
 * red, a little black) -- per the product owner's direction.
 *
 * The attribute is set before first paint by THEME_SCRIPT (inlined in the root
 * layout), so there is never a white flash on a dark page. Client navigation
 * between surfaces is handled by <ThemeController>.
 */
export type Theme = 'light' | 'dark';

export const THEME_KEY = 'nexus-theme';
export const THEME_EVENT = 'nexus-theme-change';

/** Surface default when the visitor has not chosen. */
export const routeDefault = (pathname: string): Theme => (pathname === '/' ? 'dark' : 'light');

export function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  if (typeof document === 'undefined') return 'light';
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (root.getAttribute('data-theme') === theme) return;
  root.setAttribute('data-theme', theme);
  window.dispatchEvent(new CustomEvent(THEME_EVENT, {detail: theme}));
}

/** The toggle: an explicit choice, remembered across visits and surfaces. */
export function chooseTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private mode: the choice still applies for this page */
  }
  applyTheme(theme);
}

export function onThemeChange(fn: (t: Theme) => void): () => void {
  const handler = (e: Event) => fn((e as CustomEvent<Theme>).detail);
  window.addEventListener(THEME_EVENT, handler);
  return () => window.removeEventListener(THEME_EVENT, handler);
}

/**
 * Runs inline in <head>, before first paint. Kept dependency-free and
 * self-contained because it executes before any bundle has loaded.
 */
export const THEME_SCRIPT = `(function(){try{var k='${THEME_KEY}',p=localStorage.getItem(k),t=(p==='light'||p==='dark')?p:(location.pathname==='/'?'dark':'light');document.documentElement.setAttribute('data-theme',t)}catch(e){document.documentElement.setAttribute('data-theme',location.pathname==='/'?'dark':'light')}})();`;
