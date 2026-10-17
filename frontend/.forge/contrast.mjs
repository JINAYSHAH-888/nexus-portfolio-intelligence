import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const p = await ctx.newPage();
await p.goto('http://localhost:3100/', { waitUntil: 'networkidle' });
await p.waitForTimeout(2500);
const r = await new AxeBuilder({ page: p }).withRules(['color-contrast']).analyze();
for (const v of r.violations) for (const n of v.nodes) {
  console.log('TARGET :', n.target.join(' '));
  console.log('HTML   :', n.html.slice(0, 200));
  console.log('WHY    :', (n.any[0]?.message || '').slice(0, 300));
  console.log('DATA   :', JSON.stringify(n.any[0]?.data));
  console.log('---');
}
await b.close();
