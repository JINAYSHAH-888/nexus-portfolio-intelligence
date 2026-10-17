#!/usr/bin/env node
/**
 * audit.mjs — accessibility + vitals + budget in one pass.
 *   npm i -D @axe-core/playwright playwright
 *   node audit.mjs --url http://localhost:3000
 */
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i === -1 ? d : process.argv[i+1]; };
const url = arg("url", "http://localhost:3000");
const BUDGET = { script: 150, stylesheet: 60, font: 120, image: 400, total: 900,
                 lcp: 2000, cls: 0.08 };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

/* MEASUREMENT FIX. This summed the `content-length` header, which the Next
 * dev server never sends for JS/CSS because it streams chunked. Every budget
 * therefore reported 0KB and the gate could not fail -- "PASS script 0KB" on
 * a React app is not a pass, it is a non-measurement.
 *
 * PerformanceResourceTiming.encodedBodySize reports what actually came over
 * the wire, chunked or not, and counts each request separately so a
 * double-fetched font is visible rather than hidden. */
const bytes = { script: 0, stylesheet: 0, font: 0, image: 0, other: 0 };

await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
{
  const measured = await page.evaluate(() =>
    performance.getEntriesByType("resource").map((e) => ({
      type: e.initiatorType,
      name: e.name,
      size: e.encodedBodySize || e.transferSize || 0,
    })),
  );
  for (const r of measured) {
    const n = r.name.toLowerCase();
    const k = /\.(woff2?|ttf|otf)(\?|$)/.test(n) ? "font"
            : /\.(css)(\?|$)/.test(n) || r.type === "link" ? "stylesheet"
            : /\.(js|mjs)(\?|$)/.test(n) || r.type === "script" ? "script"
            : /\.(png|jpe?g|gif|svg|webp|avif)(\?|$)/.test(n) || r.type === "img" ? "image"
            : "other";
    bytes[k] += r.size;
  }
}

const vitals = await page.evaluate(() => new Promise((resolve) => {
  const v = { lcp: 0, cls: 0 };
  new PerformanceObserver((l) => { for (const e of l.getEntries()) v.lcp = e.startTime; })
    .observe({ type: "largest-contentful-paint", buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) v.cls += e.value; })
    .observe({ type: "layout-shift", buffered: true });
  setTimeout(() => resolve(v), 2500);
}));

const axe = await new AxeBuilder({ page })
  .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();

await browser.close();

const kb = (n) => Math.round(n / 1024);
const line = (ok, s) => console.log(`${ok ? "PASS" : "FAIL"}  ${s}`);
const total = Object.values(bytes).reduce((a, b) => a + b, 0);

console.log("\n== BUDGET ==");
for (const k of ["script", "stylesheet", "font", "image"])
  line(kb(bytes[k]) <= BUDGET[k], `${k.padEnd(11)} ${kb(bytes[k])}KB / ${BUDGET[k]}KB`);
line(kb(total) <= BUDGET.total, `${"total".padEnd(11)} ${kb(total)}KB / ${BUDGET.total}KB`);

console.log("\n== VITALS ==");
line(vitals.lcp <= BUDGET.lcp, `LCP ${Math.round(vitals.lcp)}ms / ${BUDGET.lcp}ms`);
line(vitals.cls <= BUDGET.cls, `CLS ${vitals.cls.toFixed(3)} / ${BUDGET.cls}`);
console.log("  note: INP requires real interaction — measure in the field with web-vitals");

console.log("\n== ACCESSIBILITY ==");
const crit = axe.violations.filter(v => ["critical", "serious"].includes(v.impact));
line(crit.length === 0, `${crit.length} critical/serious violations (${axe.violations.length} total)`);
for (const v of axe.violations)
  console.log(`  [${v.impact}] ${v.id} — ${v.nodes.length}× — ${v.help}`);

const failed = kb(total) > BUDGET.total || vitals.lcp > BUDGET.lcp
             || vitals.cls > BUDGET.cls || crit.length > 0;
process.exit(failed ? 1 : 0);
