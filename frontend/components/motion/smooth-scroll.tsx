'use client';

import {useEffect} from 'react';
import {invalidateLayout, sampleScroll} from '@/lib/scroll-store';

/**
 * Lenis smooth scroll + the scroll bus, driven by GSAP's ticker so there is
 * ONE frame callback in the app that owns scroll.
 *
 * Order inside that callback is the whole point:
 *     lenis.raf()  ->  sampleScroll()
 * Lenis steps the eased position first, then the shared snapshot is published.
 * The gem (R3F), the telemetry rail and the grid all read that one snapshot, so
 * DOM and WebGL cannot drift a frame apart during a fast scroll.
 *
 * DESIGN.md: lerp .08, native scrollbar stays VISIBLE, no scroll-jacking --
 * Lenis only smooths the existing scroll, it never takes it over. Keyboard,
 * space bar and Find-in-page keep working.
 *
 * Under prefers-reduced-motion Lenis is not created at all (smoothing is still
 * motion the user did not ask for), but the snapshot is STILL published from
 * native scroll -- the gem's scroll channel stays live, only its idle motion
 * and the smoothing stop.
 */
export default function SmoothScroll() {
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let lenis: import('lenis').default | null = null;
    let tickerFn: (() => void) | null = null;
    let cancelled = false;
    let ro: ResizeObserver | null = null;
    let offRefresh: (() => void) | null = null;
    let offClick: (() => void) | null = null;

    (async () => {
      const [{gsap}, {ScrollTrigger}] = await Promise.all([
        import('gsap'),
        import('gsap/ScrollTrigger'),
      ]);
      if (cancelled) return;
      gsap.registerPlugin(ScrollTrigger);

      if (!reduced) {
        const {default: Lenis} = await import('lenis');
        if (cancelled) return;
        lenis = new Lenis({lerp: 0.08, wheelMultiplier: 1, touchMultiplier: 1.5});
        lenis.on('scroll', ScrollTrigger.update);
      }

      /* In-page links scroll THROUGH Lenis, so the gem travels instead of
       * teleporting. Handled here rather than with Lenis's `anchors` option:
       * that measures from Lenis's own scroll value, which goes stale when
       * the browser restores a mid-page position on reload -- the link then
       * lands in the wrong movement. The target is measured from the real
       * native scroll position instead, which is always correct. */
      const onClick = (e: MouseEvent) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
        const a = (e.target as Element | null)?.closest?.('a[href^="#"]') as HTMLAnchorElement | null;
        const id = a?.getAttribute('href')?.slice(1);
        const el = id ? document.getElementById(id) : null;
        if (!el) return;
        e.preventDefault();
        const top = el.getBoundingClientRect().top + window.scrollY;
        if (lenis) lenis.scrollTo(top, {duration: 1.4});
        else window.scrollTo({top});
        // Focus follows the jump for keyboard and screen-reader users.
        el.setAttribute('tabindex', '-1');
        el.focus({preventScroll: true});
      };
      document.addEventListener('click', onClick);
      offClick = () => document.removeEventListener('click', onClick);

      tickerFn = () => {
        lenis?.raf(performance.now());
        sampleScroll();
      };
      gsap.ticker.add(tickerFn);
      gsap.ticker.lagSmoothing(0);

      // Pins insert pin-spacers: every refresh changes the document height
      // and every movement's position, so the stations must be re-measured.
      const onRefresh = () => invalidateLayout();
      ScrollTrigger.addEventListener('refresh', onRefresh);
      offRefresh = () => ScrollTrigger.removeEventListener('refresh', onRefresh);

      // Late layout changes ScrollTrigger does not see: web fonts swapping in,
      // images decoding. Coalesced to one refresh per frame.
      let queued = false;
      ro = new ResizeObserver(() => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => {
          queued = false;
          ScrollTrigger.refresh();
        });
      });
      ro.observe(document.body);
      invalidateLayout();
    })();

    return () => {
      cancelled = true;
      ro?.disconnect();
      offRefresh?.();
      offClick?.();
      (async () => {
        const {gsap} = await import('gsap');
        if (tickerFn) gsap.ticker.remove(tickerFn);
        lenis?.destroy();
      })();
    };
  }, []);

  return null;
}
