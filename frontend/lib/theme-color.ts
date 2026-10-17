/**
 * Read a design token's resolved colour as a hex string.
 *
 * WHY THIS EXISTS. Most components can write `var(--accent)` straight into a
 * CSS property. WebGL uniforms and canvas 2D contexts cannot — they need a
 * concrete value. Hardcoding one duplicates the palette into TypeScript, where
 * it silently drifts the moment a ramp is regenerated. DESIGN.md's rule is that
 * a hex literal in a component is a bug, and a copy of the palette in a
 * constants file is the same bug with extra steps.
 *
 * So the value is resolved from the live CSS custom property at runtime, and
 * the browser does the colour-space conversion by RASTERISING it: paint the
 * token onto a 1x1 sRGB canvas and read the pixel back. No conversion maths to
 * get wrong, no second copy of the palette, and it follows a theme switch.
 *
 * WHY NOT getComputedStyle(el).color -- the previous approach, and a real bug.
 * It assumed computed colours come back as `rgb(...)`. Modern Chrome preserves
 * the authored space, so an oklch token comes back as `oklch(0.648 0.19 25)`;
 * the rgb branch was skipped and the FALLBACK returned instead -- `rgb(0 0 0)`
 * for any token not in the table. The ruby N was being tinted pure black and
 * rendered as chrome. A canvas pixel is sRGB by definition, in every browser.
 */

/**
 * SSR / pre-hydration fallbacks. They live HERE rather than at each call site
 * so a component never contains a colour literal, and so there is exactly one
 * place to update if a ramp is regenerated. Each mirrors its token's step-9
 * ("solid fill") or step-12 ("high contrast") value from the generated CSS.
 */
const FALLBACK: Record<string, string> = {
  '--accent': '#ec5250',
  '--accent-hover': '#dc4343',
  '--brand-11': '#ab3b38',
  '--n-1': '#0b0d12',
  '--n-2': '#12141a',
  '--n-12': '#eef1f6',
  '--text': '#eef1f6',
  '--bg': '#0b0d12',
};

const cache = new Map<string, string>();

function rgbToHex(rgb: string): string {
  const m = rgb.match(/-?[\d.]+/g);
  if (!m || m.length < 3) return '#000000';
  const [r, g, b] = m.slice(0, 3).map((n) => {
    const v = Math.round(Number(n));
    return Math.max(0, Math.min(255, v));
  });
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

/**
 * @param token CSS custom property name, e.g. '--accent' or '--n-1'
 * @param fallback used during SSR and if the token is not defined
 */
export function tokenColor(token: string, fallbackOverride?: string): string {
  const fallback = fallbackOverride ?? FALLBACK[token] ?? 'rgb(0 0 0)';
  if (typeof window === 'undefined') return fallback;

  const cached = cache.get(token);
  if (cached) return cached;

  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(token)
    .trim();
  if (!raw) return fallback;

  const hex = rasterise(raw) ?? fallback;
  cache.set(token, hex);
  return hex;
}

let ctx: CanvasRenderingContext2D | null = null;
/** An unlikely colour, so an unparseable token is detectable (an invalid
 *  fillStyle assignment is silently ignored, leaving the previous value). */
const SENTINEL = '#010203';

/** Paint any CSS colour onto a 1x1 sRGB canvas and read the pixel back. */
function rasterise(color: string): string | null {
  if (!ctx) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1;
    ctx = canvas.getContext('2d', {willReadFrequently: true});
  }
  if (!ctx) return null;
  ctx.fillStyle = SENTINEL;
  ctx.fillStyle = color;
  if (ctx.fillStyle === SENTINEL && color.toLowerCase() !== SENTINEL) return null;
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return rgbToHex(`rgb(${r}, ${g}, ${b})`);
}

/** Clear the cache after a theme change so tokens re-resolve. */
export function clearTokenColorCache() {
  cache.clear();
}
