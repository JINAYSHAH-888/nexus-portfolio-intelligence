# Nexus Portfolio Intelligence — Frontend

Next.js 15 · React 19 · TypeScript · Tailwind v4 · GSAP · React Three Fiber.

```bash
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1" > .env.local
npm run dev -- -p 3400     # http://localhost:3400
```

Omit `NEXT_PUBLIC_API_BASE_URL` to run in demo mode on bundled sample data.

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server |
| `npm run build` / `npm start` | Production build / serve |
| `npm run lint` | ESLint |
| `node scripts/build-overlap-dataset.mjs` | Regenerate verified landing facts from factsheet holdings |
| `node scripts/build-tokens.mjs` | Regenerate colour tokens (WCAG AA gated) |

Design contract: [`../DESIGN.md`](../DESIGN.md). Full project docs: [`../README.md`](../README.md).
