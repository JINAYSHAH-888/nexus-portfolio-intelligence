/**
 * Where the ruby N is on screen, published once per rendered frame by the
 * scene and read by DOM overlays (the tracking reticle). Same pattern as the
 * scroll bus: a plain mutable snapshot plus listeners that write straight to
 * the DOM, so nothing re-renders React per frame.
 */
export type GemFrame = {
  /** Centre, in CSS px from the viewport's top-left. */
  x: number;
  y: number;
  /** Projected half-height of the letterform, CSS px. */
  r: number;
  /** Current yaw in radians (unwrapped). */
  yaw: number;
  /** The canvas opacity the scene is currently using. */
  dim: number;
};

const frame: GemFrame = {x: 0, y: 0, r: 0, yaw: 0, dim: 0};
const listeners = new Set<(f: GemFrame) => void>();

export function publishGem(x: number, y: number, r: number, yaw: number, dim: number): void {
  frame.x = x;
  frame.y = y;
  frame.r = r;
  frame.yaw = yaw;
  frame.dim = dim;
  for (const fn of listeners) fn(frame);
}

export function onGemFrame(fn: (f: GemFrame) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
