#!/usr/bin/env node
/**
 * Builds the landing page's real holdings dataset from the backend's factsheet
 * data, and VERIFIES the result against known-good figures before writing.
 *
 * Why this exists as a committed script rather than a pasted blob: the landing
 * page's whole argument is "every number traces to a real disclosure". A
 * hand-copied JSON silently rots the moment the factsheet data is refreshed,
 * and the page would go on asserting stale numbers with total confidence.
 *
 * The normalisation here MIRRORS backend/engines/overlap_engine.py exactly:
 *   _get_stock_set  -> name.strip().rstrip("*").lower(), weight > 0
 *   _load_holdings  -> drops "equity less than 1% of corpus" and
 *                      "cash and other receivables" (not real positions)
 * If the engine's rules change, this script must change with them -- that is
 * the point of the assertions at the bottom.
 *
 * Usage:  node scripts/build-overlap-dataset.mjs
 */
import {readFileSync, writeFileSync} from 'node:fs';
import {gzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {dirname, resolve} from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const DATA = resolve(HERE, '../../backend/backend/data');
const OUT = resolve(HERE, '../public/data/equity-holdings.json');

/** Pseudo-holdings the engine excludes: aggregates, not positions. */
const EXCLUDED = new Set([
  'equity less than 1% of corpus',
  'cash and other receivables',
]);

/** The engine's canonical key for a holding. */
const normalise = (name) => name.trim().replace(/\*+$/, '').toLowerCase();

const holdingsFile = JSON.parse(readFileSync(`${DATA}/sample_holdings.json`, 'utf8'));
const metaList = JSON.parse(readFileSync(`${DATA}/schemes_metadata.json`, 'utf8'));
const meta = new Map(metaList.map((m) => [m.id, m]));

/** Engine-equivalent weight map: {normalisedName: weight}. */
function weightMap(schemeId) {
  const out = new Map();
  for (const h of holdingsFile[schemeId]?.holdings ?? []) {
    const key = normalise(h.name);
    if (EXCLUDED.has(key)) continue;
    if (!(h.weight > 0)) continue;
    out.set(key, h.weight);
  }
  return out;
}

/* Equity only: debt schemes hold issuers, not stocks, so holdings-based
 * overlap is meaningless for them (the engine says the same). The >=10 floor
 * drops schemes whose factsheet disclosure is too thin to compare -- the Gilt
 * and Overnight funds disclose 2-3 lines. */
const equityIds = [...Object.keys(holdingsFile)]
  .filter((id) => meta.get(id)?.instrument_type === 'equity')
  .filter((id) => weightMap(id).size >= 10);

/* ── Intern: dedupe on the engine's normalised key, but keep the first-seen
 * original spelling for display. Index identity therefore IS engine identity,
 * so the browser can compare by integer and still render a human label. */
const stockIdx = new Map();
const stocks = [];
const sectorIdx = new Map();
const sectors = [];

function internStock(rawName) {
  const key = normalise(rawName);
  if (!stockIdx.has(key)) {
    stockIdx.set(key, stocks.length);
    stocks.push(rawName.trim().replace(/\*+$/, ''));
  }
  return stockIdx.get(key);
}
function internSector(sector) {
  if (!sector) return -1;
  if (!sectorIdx.has(sector)) {
    sectorIdx.set(sector, sectors.length);
    sectors.push(sector);
  }
  return sectorIdx.get(sector);
}

const schemes = equityIds.map((id) => {
  const seen = new Set();
  const h = [];
  for (const raw of holdingsFile[id].holdings) {
    const key = normalise(raw.name);
    if (EXCLUDED.has(key) || !(raw.weight > 0) || seen.has(key)) continue;
    seen.add(key);
    h.push([internStock(raw.name), Math.round(raw.weight * 100) / 100, internSector(raw.sector)]);
  }
  return {
    id,
    name: meta.get(id).name,
    cat: meta.get(id).category,
    h,
  };
});

const asOf = holdingsFile[equityIds[0]].as_of_date;
const payload = {asOf, stocks, sectors, schemes};

/* ══ VERIFICATION ══════════════════════════════════════════════════════════
 * Recompute the figures the landing page states, from the payload we are
 * about to write. A mismatch means the data moved under the copy, and the
 * build fails loudly rather than shipping a confident wrong number. */
const setOf = (s) => new Set(s.h.map((r) => r[0]));
const wOf = (s) => new Map(s.h.map((r) => [r[0], r[1]]));

const jaccard = (a, b) => {
  const A = setOf(a), B = setOf(b);
  if (!A.size || !B.size) return 0;
  let inter = 0;
  for (const x of A) if (B.has(x)) inter++;
  return inter / (A.size + B.size - inter);
};
const weightedOverlap = (a, b) => {
  const A = wOf(a), B = wOf(b);
  let sum = 0;
  for (const [k, v] of A) if (B.has(k)) sum += Math.min(v, B.get(k));
  return sum;
};

const pairs = [];
for (let i = 0; i < schemes.length; i++)
  for (let k = i + 1; k < schemes.length; k++)
    pairs.push({
      a: schemes[i], b: schemes[k],
      j: jaccard(schemes[i], schemes[k]),
      w: weightedOverlap(schemes[i], schemes[k]),
    });

const byWeight = [...pairs].sort((x, y) => y.w - x.w);
const avgJaccard = pairs.reduce((s, p) => s + p.j, 0) / pairs.length;

const held = new Map();
for (const s of schemes) for (const x of setOf(s)) held.set(x, (held.get(x) ?? 0) + 1);
const mostHeld = [...held.entries()].sort((a, b) => b[1] - a[1]);

const checks = [];
const expect = (label, actual, wanted) => {
  const ok = String(actual) === String(wanted);
  checks.push({label, actual, wanted, ok});
};

expect('equity schemes', schemes.length, 20);
expect('pairs', pairs.length, 190);
expect('avg jaccard', avgJaccard.toFixed(4), '0.0923');
expect('top weighted overlap %', byWeight[0].w.toFixed(2), '41.07');
expect(
  'top pair',
  `${byWeight[0].a.id} x ${byWeight[0].b.id}`,
  'nippon-large-cap-fund x nippon-quant-fund',
);
expect('runner-up weighted %', byWeight[1].w.toFixed(2), '40.59');
expect('as-of date', asOf, '2026-05-31');
// The four banks each appear in 11 of the 20 equity schemes.
const topFour = mostHeld.slice(0, 4);
expect('four banks all at 11', topFour.every(([, c]) => c === 11), true);
expect(
  'four banks are the banks',
  topFour.map(([i]) => stocks[i]).sort().join(' | '),
  'Axis Bank Limited | HDFC Bank Limited | ICICI Bank Limited | State Bank of India',
);

// How many schemes hold ALL FOUR of those banks at once (the headline).
const fourIdx = topFour.map(([i]) => i);
const holdAllFour = schemes.filter((s) => fourIdx.every((i) => setOf(s).has(i)));
expect('schemes holding all four banks', holdAllFour.length, 8);

const raw = Buffer.from(JSON.stringify(payload));
const gz = gzipSync(raw, {level: 9});

console.log('\nequity-holdings.json');
console.log(`  schemes ${schemes.length}  stocks ${stocks.length}  sectors ${sectors.length}  rows ${schemes.reduce((n, s) => n + s.h.length, 0)}`);
console.log(`  raw ${(raw.length / 1024).toFixed(1)} KB   gzip ${(gz.length / 1024).toFixed(1)} KB   as-of ${asOf}\n`);
console.log('verification against the engine');
for (const c of checks)
  console.log(`  ${c.ok ? 'PASS' : 'FAIL'}  ${c.label.padEnd(26)} ${c.actual}${c.ok ? '' : `   (expected ${c.wanted})`}`);

const failed = checks.filter((c) => !c.ok);
if (failed.length) {
  console.error(`\n${failed.length} check(s) FAILED -- refusing to write. The landing page copy cites these numbers.`);
  process.exit(1);
}

writeFileSync(OUT, JSON.stringify(payload));
console.log(`\nwrote ${OUT}\n`);

/* ── Landing facts ───────────────────────────────────────────────────────────
 * The handful of figures the landing page states in its copy, emitted as a
 * tiny static import so the opening renders real numbers in the server HTML
 * with no fetch and no layout shift. Generated from the SAME verified payload
 * as the dataset above, so copy and data cannot drift apart. */
const name = (i) => stocks[i];
const top = byWeight[0];
const sharedTop = [];
{
  const A = wOf(top.a), B = wOf(top.b);
  for (const [k, va] of A) if (B.has(k)) sharedTop.push({stock: name(k), a: va, b: B.get(k), shared: Math.min(va, B.get(k))});
  sharedTop.sort((x, y) => y.shared - x.shared);
}

// Worst equal-split 3-fund single-stock concentration across all 1,140 trios.
let trio = null;
for (let i = 0; i < schemes.length; i++)
  for (let k = i + 1; k < schemes.length; k++)
    for (let m = k + 1; m < schemes.length; m++) {
      const legs = [schemes[i], schemes[k], schemes[m]];
      const maps = legs.map(wOf);
      for (const [stock, w0] of maps[0]) {
        if (!maps[1].has(stock) || !maps[2].has(stock)) continue;
        const pct = (w0 + maps[1].get(stock) + maps[2].get(stock)) / 3;
        if (!trio || pct > trio.pct) trio = {stock, pct, legs};
      }
    }

const facts = {
  asOf,
  totalSchemes: metaList.length,
  equitySchemes: schemes.length,
  holdingsCompared: schemes.reduce((n, s) => n + s.h.length, 0),
  pairs: pairs.length,
  avgJaccard: Number(avgJaccard.toFixed(4)),
  mostHeld: mostHeld.slice(0, 4).map(([i, c]) => ({stock: name(i), count: c})),
  /** Schemes that hold every one of the four banks above. */
  holdAllFour: holdAllFour.map((s) => s.name),
  /** Schemes holding none of them -- sector and international funds. */
  holdNone: schemes.filter((s) => fourIdx.every((i) => !setOf(s).has(i))).map((s) => s.name),
  topPair: {
    a: top.a.name, b: top.b.name,
    weightedOverlap: Number(top.w.toFixed(2)),
    jaccard: Number(top.j.toFixed(3)),
    sharedCount: sharedTop.length,
    shared: sharedTop.map((r) => ({...r, shared: Number(r.shared.toFixed(2))})),
  },
  /** All 190 pairwise weighted overlaps, high to low -- the shape of the problem. */
  overlapCurve: byWeight.map((p) => Number(p.w.toFixed(2))),
  trio: {
    stock: name(trio.stock),
    pct: Number(trio.pct.toFixed(2)),
    legs: trio.legs.map((s) => ({scheme: s.name, weight: wOf(s).get(trio.stock)})),
  },
};
const FACTS = resolve(HERE, '../lib/generated/landing-facts.json');
writeFileSync(FACTS, JSON.stringify(facts, null, 1));
console.log(`wrote ${FACTS}  (${(Buffer.byteLength(JSON.stringify(facts)) / 1024).toFixed(1)} KB)\n`);

/* Emit the headline facts so the copy can be kept honest by hand too. */
console.log('headline facts now true of this dataset:');
console.log(`  four banks each held by 11 / ${schemes.length} equity schemes`);
for (const [i, c] of mostHeld.slice(0, 5)) console.log(`     ${String(c).padStart(2)}  ${stocks[i]}`);
console.log(`  highest weighted overlap  ${byWeight[0].w.toFixed(2)}%  ${byWeight[0].a.name} x ${byWeight[0].b.name}`);
