#!/usr/bin/env node
/**
 * Builds the NEUTRAL and BRAND colour ramps from the product owner's
 * reference palette, and refuses to write them unless every text/surface
 * pairing clears WCAG 2.2 AA in BOTH themes.
 *
 *   node scripts/build-tokens.mjs
 *
 * Reference (owner-supplied colour card, 2026-10-17):
 *   Black      #0B0B0B  oklch(15.0% 0      --)   dark background
 *   Off-white  #F5F2ED  oklch(96.2% 0.0074 81)   light background, dark-mode text
 *   Crimson    #8B0D1A  oklch(40.7% 0.1564 24)   deep red
 *   Vivid red  #E5262D  oklch(59.4% 0.2224 26)   accent (AgentAI reference)
 * Both reds sit on hue ~25 -- the existing brand hue -- so the identity
 * carries over; it only gets more saturated.
 *
 * Hand-placed lightness steps (Radix 12-step semantics) rather than the
 * generic generator's fixed curve: the generic curve puts light step 1 at
 * L 99.3%, which is white, not the off-white the reference asks for.
 */
import {writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';

const OUT = resolve(dirname(fileURLToPath(import.meta.url)), '../styles/tokens');

// ── colour math (OKLCH -> sRGB), gamut-mapped by reducing chroma ──────────
const enc = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);
function oklchToLinear(L, C, H) {
  const h = (H * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s];
}
const inGamut = (lin) => lin.every((c) => c >= -1e-4 && c <= 1 + 1e-4);
function mapped(L, C, H) {
  if (inGamut(oklchToLinear(L, C, H))) return C;
  let lo = 0, hi = C;
  for (let i = 0; i < 28; i++) { const mid = (lo + hi) / 2; inGamut(oklchToLinear(L, mid, H)) ? (lo = mid) : (hi = mid); }
  return lo;
}
const lum = (lin) => 0.2126 * Math.max(0, lin[0]) + 0.7152 * Math.max(0, lin[1]) + 0.0722 * Math.max(0, lin[2]);
const hex = (lin) => '#' + lin.map((c) => Math.round(Math.min(1, Math.max(0, enc(c))) * 255).toString(16).padStart(2, '0')).join('');
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

// [L%, C] per step; hue per ramp.
const RAMPS = {
  n: {
    hue: 80,
    light: [[96.2, .0074], [94.3, .008], [91.8, .0085], [89.3, .009], [86.7, .009], [83.6, .009],
            [79.0, .009], [71.5, .009], [57.5, .008], [52.5, .008], [44.0, .008], [18.0, .006]],
    dark:  [[15.0, .0015], [18.2, .0025], [21.6, .0035], [24.6, .004], [27.6, .0045], [31.2, .005],
            [36.2, .005], [44.0, .0055], [58.0, .006], [63.0, .006], [75.0, .0065], [95.5, .0074]],
  },
  brand: {
    hue: 25,
    light: [[97.4, .012], [95.4, .024], [92.4, .047], [88.8, .072], [84.8, .097], [79.8, .122],
            [73.2, .152], [66.0, .186], [55.5, .215], [50.5, .205], [40.7, .156], [28.5, .105]],
    dark:  [[17.2, .032], [19.8, .046], [24.2, .076], [27.6, .096], [31.0, .112], [35.4, .128],
            [41.0, .152], [48.5, .180], [55.5, .215], [61.5, .215], [72.0, .175], [92.0, .042]],
  },
};

const build = (name, theme) =>
  RAMPS[name][theme].map(([L, C], i) => {
    const c = mapped(L / 100, C, RAMPS[name].hue);
    const lin = oklchToLinear(L / 100, c, RAMPS[name].hue);
    return {step: i + 1, css: `oklch(${L.toFixed(1)}% ${c.toFixed(4)} ${RAMPS[name].hue})`, lin, hex: hex(lin)};
  });

const T = {};
for (const theme of ['light', 'dark']) T[theme] = {n: build('n', theme), brand: build('brand', theme)};

// Text that sits ON the accent fill (buttons): the off-white in both themes.
const onAccent = oklchToLinear(0.962, 0.0074, 80.7);

// ── AA gate ────────────────────────────────────────────────────────────────
const checks = [];
for (const theme of ['light', 'dark']) {
  const {n, brand} = T[theme];
  const bg = n[0].lin, sub = n[1].lin;
  const add = (label, a, b, min) => checks.push({theme, label, ratio: contrast(a, b), min});
  add('text on bg', n[11].lin, bg, 7);
  add('text on subtle', n[11].lin, sub, 7);
  add('secondary text on bg', n[10].lin, bg, 4.5);
  add('secondary text on subtle', n[10].lin, sub, 4.5);
  add('accent text on bg', brand[10].lin, bg, 4.5);
  add('button text on accent', onAccent, brand[8].lin, 4.5);
  add('accent fill vs bg (UI)', brand[8].lin, bg, 3);
  add('strong line vs bg (UI)', n[7].lin, bg, 1.8);
}
let fail = 0;
console.log('WCAG 2.2 AA');
for (const c of checks) {
  const ok = c.ratio >= c.min;
  if (!ok) fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${c.theme.padEnd(5)} ${c.label.padEnd(26)} ${c.ratio.toFixed(2)}:1  (min ${c.min})`);
}
if (fail) { console.error(`\n${fail} pairing(s) fail -- tokens NOT written.`); process.exit(1); }

// ── write ──────────────────────────────────────────────────────────────────
const block = (sel, name, ramp, pad = '') =>
  `${pad}${sel} {\n${ramp.map((s) => `${pad}  --${name}-${s.step}: ${s.css};`).join('\n')}\n${pad}}`;
const file = (name, prefix) => `/* ${name} -- built by scripts/build-tokens.mjs from the owner's reference palette.
 * Do not hand-edit: change the ramp in the script and re-run it (it gates on WCAG AA). */
${block(':root', prefix, T.light[name])}

${block(':root[data-theme="dark"]', prefix, T.dark[name])}

/* sRGB fallback for engines without oklch() */
@supports not (color: oklch(0% 0 0)) {
${block('  :root', prefix, T.light[name].map((s) => ({...s, css: s.hex})))}
${block('  :root[data-theme="dark"]', prefix, T.dark[name].map((s) => ({...s, css: s.hex})))}
}
`;
writeFileSync(`${OUT}/color-neutral.css`, file('n', 'n'));
writeFileSync(`${OUT}/color-brand.css`, file('brand', 'brand'));
console.log('\nwrote color-neutral.css, color-brand.css');
for (const th of ['light', 'dark']) console.log(`  ${th.padEnd(5)} bg ${T[th].n[0].hex}  text ${T[th].n[11].hex}  accent ${T[th].brand[8].hex}  accent-text ${T[th].brand[10].hex}`);
