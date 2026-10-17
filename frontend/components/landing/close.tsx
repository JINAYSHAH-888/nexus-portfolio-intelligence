import Link from 'next/link';
import Movement from './movement';
import ScrollText from './scroll-text';

/**
 * Movement 05. The ask, on a lit crimson slab -- with the gem back where it
 * started, on the right. The slab holds the left half: the right belongs to
 * the stone.
 */
export default function Close() {
  return (
    <Movement id="close" density="none">
      <div className="grid-page min-h-dvh items-center py-26">
        <div className="crimson-panel col-span-12 px-8 py-16 sm:px-14 sm:py-20 lg:col-span-6">
          <span aria-hidden="true" className="echo-word echo-word--panel pointer-events-none absolute -bottom-10 -right-6 select-none font-display font-bold uppercase leading-none">
            Own
          </span>
          <p className="relative font-mono text-step--2 uppercase tracking-[0.12em] opacity-80">Your turn</p>
          <ScrollText
            id="close-title"
            text="Know what you actually own."
            className="relative mt-4 max-w-[12ch] font-display text-display font-semibold"
          />
          <p className="relative mt-8 max-w-[40ch] text-step-1 opacity-90">
            Ten fields. No card. Every assumption on the page.
          </p>
          <div className="relative mt-10 flex flex-wrap gap-3">
            <Link href="/register" className="btn-ivory">
              Create free profile
            </Link>
            <Link href="/login" className="btn-glass glass-pill text-on-accent">
              <span className="relative z-[2]">Sign in</span>
            </Link>
          </div>
        </div>
      </div>
    </Movement>
  );
}
