'use client';

import {Moon, Sun} from 'lucide-react';
import {chooseTheme} from '@/lib/theme';
import {useTheme} from './use-theme';

/**
 * Light/dark switch as a liquid-glass pill with a sliding knob. A real
 * <button role="switch"> so it is keyboard- and screen-reader-operable; the
 * knob position mirrors aria-checked (checked = dark).
 */
export default function ThemeToggle({className = ''}: {className?: string}) {
  const theme = useTheme();
  const dark = theme === 'dark';

  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
      onClick={() => chooseTheme(dark ? 'light' : 'dark')}
      className={`glass-pill relative inline-flex h-11 w-[78px] shrink-0 items-center px-1.5 ${className}`}
    >
      <Sun aria-hidden="true" className="relative z-[2] ml-1 h-4 w-4 text-text-secondary" strokeWidth={1.75} />
      <Moon aria-hidden="true" className="relative z-[2] ml-auto mr-1 h-4 w-4 text-text-secondary" strokeWidth={1.75} />
      <span
        aria-hidden="true"
        className="absolute top-1.5 z-[1] h-8 w-8 rounded-full bg-accent shadow-[0_6px_18px_-4px_var(--accent)] transition-transform duration-300 ease-[cubic-bezier(.65,0,.35,1)]"
        style={{transform: dark ? 'translateX(34px)' : 'translateX(0)'}}
      />
    </button>
  );
}
