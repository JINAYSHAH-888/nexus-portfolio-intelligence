/**
 * The scroll bus.
 *
 * ONE snapshot of scroll state, written once per frame by the GSAP ticker
 * (after Lenis has stepped), and read by everything that moves: the gem, the
 * telemetry rail, the grid's progress rule. Readers never touch window.scrollY
 * themselves, so they cannot disagree about which frame it is.
 *
 * Why not React state or a zustand subscription: these values change 60 times
 * a second. Routing them through React would re-render the page every frame.
 * Readers either poll `getScroll()` inside their own frame callback (the gem,
 * in useFrame) or register a listener that writes straight to the DOM.
 *
 * VELOCITY is the signal most scroll sites miss. It is decayed rather than
 * reset, so motion driven by it settles instead of stopping dead -- which is
 * the difference between a page that feels weighted and one that feels like a
 * video being scrubbed.
 */

export type ScrollSnapshot = {
  /** Scroll offset in px (already eased by Lenis when Lenis is running). */
  y: number;
  /** Total scrollable distance in px. */
  limit: number;
  /** 0..1 over the whole document. */
  progress: number;
  /** px per frame, exponentially smoothed and decayed. Signed. */
  velocity: number;
  direction: -1 | 0 | 1;
};

export type MovementId = 'opening' | 'evidence' | 'engines' | 'ledger' | 'close';

export const MOVEMENTS: {id: MovementId; label: string}[] = [
  {id: 'opening', label: 'Opening'},
  {id: 'evidence', label: 'Evidence'},
  {id: 'engines', label: 'Engines'},
  {id: 'ledger', label: 'Ledger'},
  {id: 'close', label: 'Close'},
];

/** Where each movement sits in progress-space, re-measured on every refresh. */
export type MovementSpan = {id: MovementId; start: number; end: number};

const snap: ScrollSnapshot = {y: 0, limit: 1, progress: 0, velocity: 0, direction: 0};
let spans: MovementSpan[] = [];
let lastY = 0;
let limitDirty = true;

const listeners = new Set<(s: ScrollSnapshot) => void>();
const spanListeners = new Set<(s: MovementSpan[]) => void>();
const elements = new Map<MovementId, HTMLElement>();

/** Imperative read. Use inside render loops -- never subscribe React to it. */
export const getScroll = (): Readonly<ScrollSnapshot> => snap;
export const getSpans = (): readonly MovementSpan[] => spans;

/**
 * Advance the snapshot one frame. Called from exactly one place (the GSAP
 * ticker in SmoothScroll), immediately after Lenis has stepped.
 */
export function sampleScroll(): void {
  if (typeof window === 'undefined') return;

  if (limitDirty) {
    // scrollHeight forces layout, so it is cached and re-read only when the
    // document actually changes size (ResizeObserver / ScrollTrigger refresh).
    snap.limit = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
    limitDirty = false;
  }

  const y = window.scrollY;
  const raw = y - lastY;
  lastY = y;

  // EMA toward this frame's delta: smooths wheel-notch spikes on the way up,
  // and decays geometrically to zero once scrolling stops.
  snap.velocity = snap.velocity * 0.82 + raw * 0.18;
  if (Math.abs(snap.velocity) < 0.01) snap.velocity = 0;

  snap.y = y;
  snap.progress = Math.min(1, Math.max(0, y / snap.limit));
  snap.direction = raw > 0 ? 1 : raw < 0 ? -1 : snap.direction;

  for (const fn of listeners) fn(snap);
}

/** DOM readers: called every frame with the shared snapshot. */
export function onScrollFrame(fn: (s: ScrollSnapshot) => void): () => void {
  listeners.add(fn);
  fn(snap);
  return () => listeners.delete(fn);
}

export function onSpans(fn: (s: MovementSpan[]) => void): () => void {
  spanListeners.add(fn);
  fn(spans);
  return () => spanListeners.delete(fn);
}

/** Movements register their root element so their spans can be measured. */
export function registerMovement(id: MovementId, el: HTMLElement | null): void {
  if (el) elements.set(id, el);
  else elements.delete(id);
  invalidateLayout();
}

/**
 * Re-measure document height and every movement's span. Call after anything
 * that changes layout -- ScrollTrigger pins add pin-spacer height, so this
 * MUST run on ScrollTrigger's 'refresh' event or every station the gem
 * travels to would be keyed to the pre-pin document.
 */
export function invalidateLayout(): void {
  if (typeof window === 'undefined') return;
  limitDirty = true;
  const limit = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  const vh = window.innerHeight;
  const next: MovementSpan[] = [];
  for (const {id} of MOVEMENTS) {
    const el = elements.get(id);
    if (!el) continue;
    const top = el.getBoundingClientRect().top + window.scrollY;
    next.push({
      id,
      start: Math.min(1, Math.max(0, top / limit)),
      // "end" = the moment the movement's bottom edge reaches the bottom of
      // the viewport, i.e. the last frame it still fills the screen.
      end: Math.min(1, Math.max(0, (top + el.offsetHeight - vh) / limit)),
    });
  }
  spans = next;
  for (const fn of spanListeners) fn(spans);
}
