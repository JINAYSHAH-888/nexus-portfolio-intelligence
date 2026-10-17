#!/usr/bin/env node
/**
 * capture.mjs — screenshot a running site at the rubric viewports, both themes.
 *   npx playwright install chromium
 *   node capture.mjs --url http://localhost:3000 --out .forge/shots
 */
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i === -1 ? d : process.argv[i+1]; };
const url = arg("url", "http://localhost:3000");
const out = arg("out", ".forge/shots");
const VIEWPORTS = [
  { name: "mobile",  width: 390,  height: 844 },
  { name: "tablet",  width: 768,  height: 1024 },
  { name: "desktop", width: 1440, height: 900 },
];

mkdirSync(out, { recursive: true });
const browser = await chromium.launch();

for (const vp of VIEWPORTS) {
  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      colorScheme: scheme,
      deviceScaleFactor: 2,
    });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: "networkidle" });
    await page.waitForTimeout(1200);                 // let entry animations settle
    await page.screenshot({ path: `${out}/${vp.name}-${scheme}.png`, fullPage: true });
    await ctx.close();
    console.log(`captured ${vp.name}-${scheme}`);
  }
}

// reduced-motion pass — content must still be visible
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
const page = await ctx.newPage();
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(400);
const invisible = await page.evaluate(() =>
  [...document.querySelectorAll("section,h1,h2,p,img,article")]
    .filter(el => { const s = getComputedStyle(el); return parseFloat(s.opacity) < 0.1 || s.visibility === "hidden"; })
    .length);
await page.screenshot({ path: `${out}/reduced-motion.png`, fullPage: true });
console.log(invisible > 0
  ? `FAIL  ${invisible} elements stuck invisible under prefers-reduced-motion`
  : `PASS  reduced-motion: all content visible`);

await browser.close();
