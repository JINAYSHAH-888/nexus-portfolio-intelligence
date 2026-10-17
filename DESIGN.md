# DESIGN.md

## Direction
- **Archetype:** cinematic-dark × liquid glass (owner-directed, 2026-10-17).
  Supersedes engineered-precision for SURFACES; its data discipline (mono
  labels, tabular figures, honest copy) is kept.
- **One-line intent:** a crimson stone in the dark, behind Apple-grade glass —
  premium and dramatic, while every number on it stays sourced and honest.
- **References (owner-supplied):** AgentAI (red glow object on black, glass
  stat cards, pill nav with a lit active item), arthub (black/red agency),
  "Night has come" (blurred red type), the red/black team poster (crimson
  slabs), Apple liquid glass buttons and cards, the Black / Crimson /
  Off-white colour card.
- **Themes:** light and dark are full peers, switched by a liquid-glass
  toggle and remembered. With no choice made: the **landing is dark**
  (black, crimson, a little off-white); the **app is light** (off-white, a
  little red, a little black). See `lib/theme.ts`.

## Color (OKLCH) — built by `frontend/scripts/build-tokens.mjs` (gates WCAG AA)
| Token | Light | Dark |
|---|---|---|
| `--bg` | #F5F2ED off-white | #0B0B0A black |
| `--text` | #13110F | #F3F0EB |
| `--accent` (fills) | #D41828 crimson | #D41828 |
| `--accent-text` | #8B0E18 deep crimson | #FF716B |
| `--display-accent` (giant words) | #8B0E18 | brand-10 (4.76:1 on black) |
- **Brand hue 25** (both reference reds sit on it). **Neutral hue 80**, chroma
  ≤ 0.009: warm off-white and true black — no pink cast.
- `--on-accent` (off-white) is the text colour on every red fill, both themes.
- Fixed, unthemed reds: `--gem-*` (the 3D stone) and `--crimson-hi/mid/lo`
  (the close slab) — so those never shift or lose contrast on theme change.
- AA verified for every text/surface pair in both themes (16 checks).

## Materials
- **Liquid glass** (`.glass`, `.glass-pill`): frosted fill + 20px backdrop
  blur + 170% saturate, a 1px specular rim (bright top-left), inner glow,
  soft cast shadow. Falls back to solid under `prefers-reduced-transparency`.
- **Buttons:** `.btn-ember` (lit crimson pill, primary), `.btn-glass` (glass
  pill, secondary), `.btn-ivory` (on crimson), `.btn` (dense app controls).
- **Radius:** glass cards 24px, pills full, crimson slab 32px. Dense data
  tables and app controls keep 2px.
- **Shadows/glow:** allowed for glass, the gem bloom and the crimson slab
  only. Never on data rows.

## Type
- Display "Archivo Variable" 600, tracking −0.04em at display sizes
  (`--text-display`, `--text-display-2`); body "IBM Plex Sans"; mono "IBM
  Plex Mono" for every label and figure, tabular.
- Accent words in `text-display-accent`. Blurred echo words (`.echo-word`)
  are decoration only, always `aria-hidden`.

## Motion
- The ruby N: one persistent canvas, station track, idle sway that never
  stops, velocity lean; red bloom follows it (`GemBloom`).
- Content entrance is CSS (`.enter`, `.rise`), never a JS gate on opacity.
- Scroll-converging headings (`ScrollText`, adapted from Skiper UI).
- Every animation over 200ms has a reduced-motion path.

## Interaction & accessibility
- Focus: 2px accent outline, 2px offset, never removed. Touch targets ≥ 44px.
- Theme toggle is a real `role="switch"`.
- Glass is only placed over the page background, never over imagery, so
  text on it holds AA.

## Content
- Every figure is generated and verified from source data
  (`scripts/build-overlap-dataset.mjs`) or an engine constant — never typed.
- Probabilities are estimates with ranges; never print 100% or 0%
  (`formatProbability`).
- Voice: says what was measured and what was not.
