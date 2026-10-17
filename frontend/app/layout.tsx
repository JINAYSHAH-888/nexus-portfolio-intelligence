import type {Metadata} from 'next';
// Type stack per DESIGN.md: Archivo (display) / IBM Plex Sans (body) /
// IBM Plex Mono (every label and every numeric).
// Archivo is variable, so one file covers 100-900 and the width axis.
import '@fontsource-variable/archivo';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import '@fontsource/ibm-plex-mono/600.css';
import Providers from '@/components/providers';
import SmoothScroll from '@/components/motion/smooth-scroll';
import ThemeController from '@/components/theme/theme-controller';
import {THEME_SCRIPT} from '@/lib/theme';
import './globals.css';

export const metadata: Metadata = {
  title: 'Nexus Portfolio Intelligence',
  description:
    'Mutual fund portfolio analytics and retirement simulation platform for Indian retail investors.',
};

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  // Zoom is never disabled.
  maximumScale: 5,
};

export default function RootLayout({children}: {children: React.ReactNode}) {
  return (
    // data-theme is set by THEME_SCRIPT before first paint, not here: the
    // right value depends on the visitor's saved choice and the route.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{__html: THEME_SCRIPT}} />
      </head>
      <body className="min-h-dvh bg-bg text-text antialiased" suppressHydrationWarning>
        <ThemeController />
        {/* Skip link — first focusable element on the page. */}
        <a
          href="#main"
          className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:left-4 focus-visible:top-4 focus-visible:z-[1000] focus-visible:bg-bg-component focus-visible:px-4 focus-visible:py-2 focus-visible:text-text"
        >
          Skip to main content
        </a>
        <SmoothScroll />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
