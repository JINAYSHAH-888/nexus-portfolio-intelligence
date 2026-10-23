# Frontend

Next.js 15 (App Router), React 19, TypeScript, Tailwind v4 (CSS-first `@theme inline`), TanStack Query, GSAP + ScrollTrigger, Lenis, Motion v12, React Three Fiber / drei, ogl. The design contract is [`DESIGN.md`](../DESIGN.md).

## Themes

- An inline head script (`THEME_SCRIPT`) sets `data-theme` before paint — no flash.
- Defaults: `/` is dark, every other route is light. An explicit choice is stored under the localStorage key `nexus-theme` and wins everywhere.
- `ThemeController` re-applies the default on client navigation; `useTheme` is built on `useSyncExternalStore`.

## Tokens

`scripts/build-tokens.mjs` generates OKLCH ramps (neutral hue 80, brand hue 25) and fails the build if any of 16 contrast pairs miss WCAG AA.

| | Light | Dark |
|---|---|---|
| Background | `#F5F2ED` | `#0B0B0A` |
| Accent | `#D41828` | `#D41828` |
| Accent text | `#8B0E18` | `#FF716B` |

Components use semantic tokens only — a hex literal in a component is a bug.

## Surfaces

Liquid glass: `.glass`, `.glass-pill` (backdrop blur + masked specular rim). Buttons: `.btn-ember`, `.btn-glass`, `.btn-ivory`.

## Landing scene

One persistent R3F canvas for the whole page renders the ruby N (`components/scene/ruby-n.tsx`) with `MeshTransmissionMaterial` and shards. Motion has two channels that are added, never blended: `scrub(progress)` follows a station track (`lib/scene-stations.ts`), `update(t, dt)` keeps the gem moving when scrolling stops. Mobile drops transmission; reduced motion freezes the idle channel. A `QualityGovernor` steps DPR down under load, and the canvas remounts after WebGL context loss (up to 3 times).

## Third-party components

- React Bits: GlowCursor, ElectricLogo (`components/vendor/`).
- Skiper UI: skiper31 and skiper40 (free tier) ported by hand — **attribution must appear on the public site before launch**. Do not run `shadcn add` here; shadcn is not initialised and `init` would overwrite `lib/utils.ts`.

## Commands

```bash
make dev-frontend     # next dev on :3400
npm run lint && npx tsc --noEmit
npm run build
```
