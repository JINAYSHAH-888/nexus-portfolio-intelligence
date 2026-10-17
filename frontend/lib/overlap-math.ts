/**
 * Overlap math — a faithful port of backend/engines/overlap_engine.py.
 *
 * This runs in the browser so the landing page can show the REAL engine's
 * output on real factsheet holdings without a backend, an account, or a
 * network round trip. The dataset it reads is generated and verified by
 * scripts/build-overlap-dataset.mjs.
 *
 * The two definitions that matter, and why they disagree:
 *
 *   jaccard          |A n B| / |A u B|  -- how many NAMES are shared
 *   weightedOverlap  sum of min(wa, wb) -- how much MONEY is shared
 *
 * They are not interchangeable, and the gap between them is the product's
 * entire argument. Nippon's Large Cap and Quant funds share only 15 holdings
 * (jaccard 0.283, which sounds mild) but 41.07% of their actual money. A 28%
 * resemblance by name is a 41% resemblance by money.
 *
 * Holdings are interned to integer ids on the engine's own normalised key
 * (lowercased, trailing asterisks stripped), so comparing by integer here is
 * exactly as correct as comparing by normalised string there.
 */

export type DatasetScheme = {
  id: string;
  name: string;
  cat: string;
  /** [stockIndex, weightPercent, sectorIndex]; sectorIndex -1 when undisclosed. */
  h: [number, number, number][];
};

export type EquityDataset = {
  /** Factsheet disclosure date for every scheme in the set. */
  asOf: string;
  stocks: string[];
  sectors: string[];
  schemes: DatasetScheme[];
};

export type SharedHolding = {
  stock: number;
  /** Weight in each scheme, in the order the schemes were passed. */
  weights: number[];
  /** The overlapping portion: min across the schemes, in percentage points. */
  shared: number;
};

export type PairReadout = {
  a: DatasetScheme;
  b: DatasetScheme;
  jaccard: number;
  /** Percentage points of portfolio weight held in common. */
  weightedOverlap: number;
  sharedCount: number;
  shared: SharedHolding[];
};

export type PortfolioReadout = {
  schemes: DatasetScheme[];
  /** Mean pairwise Jaccard across every pair in the portfolio. */
  avgJaccard: number;
  /** The pair sharing the most money. Null for a single scheme. */
  worstPair: PairReadout | null;
  /** Stocks reached through more than one scheme, worst exposure first. */
  duplicated: {stock: number; inSchemes: number; combinedPct: number}[];
  /** Combined weight of the single most concentrated stock, equal-split. */
  topExposure: {stock: number; pct: number; inSchemes: number} | null;
};

const weightsOf = (s: DatasetScheme) => new Map(s.h.map((r) => [r[0], r[1]]));
const namesOf = (s: DatasetScheme) => new Set(s.h.map((r) => r[0]));

/** |A n B| / |A u B| — shared NAMES, ignoring how much is in them. */
export function jaccard(a: DatasetScheme, b: DatasetScheme): number {
  const A = namesOf(a);
  const B = namesOf(b);
  if (!A.size || !B.size) return 0;
  let inter = 0;
  for (const x of A) if (B.has(x)) inter++;
  return inter / (A.size + B.size - inter);
}

/**
 * Sum of min(wa, wb) over shared holdings, in percentage points.
 *
 * min() is the right operator: if one fund holds 9% of a stock and another
 * holds 3%, only 3 points are genuinely duplicated exposure -- the remaining
 * 6 are a position the second fund simply does not have.
 */
export function weightedOverlap(a: DatasetScheme, b: DatasetScheme): number {
  const A = weightsOf(a);
  const B = weightsOf(b);
  let sum = 0;
  for (const [k, v] of A) {
    const other = B.get(k);
    if (other !== undefined) sum += Math.min(v, other);
  }
  return sum;
}

/** Shared holdings between two schemes, largest shared portion first. */
export function sharedHoldings(a: DatasetScheme, b: DatasetScheme): SharedHolding[] {
  const A = weightsOf(a);
  const B = weightsOf(b);
  const out: SharedHolding[] = [];
  for (const [stock, wa] of A) {
    const wb = B.get(stock);
    if (wb === undefined) continue;
    out.push({stock, weights: [wa, wb], shared: Math.min(wa, wb)});
  }
  return out.sort((x, y) => y.shared - x.shared);
}

export function pairReadout(a: DatasetScheme, b: DatasetScheme): PairReadout {
  const shared = sharedHoldings(a, b);
  return {
    a,
    b,
    jaccard: jaccard(a, b),
    weightedOverlap: weightedOverlap(a, b),
    sharedCount: shared.length,
    shared,
  };
}

/**
 * Full readout for a portfolio of 1..n schemes held in equal proportion.
 *
 * Equal weighting is stated rather than inferred: a visitor has not told us
 * their allocation, and assuming one would be fabricating an input. It is also
 * the fairest illustration -- it is what someone gets by splitting a lump sum
 * across the funds they picked.
 */
export function analysePortfolio(schemes: DatasetScheme[]): PortfolioReadout {
  if (schemes.length === 0) {
    return {schemes, avgJaccard: 0, worstPair: null, duplicated: [], topExposure: null};
  }

  const pairs: PairReadout[] = [];
  for (let i = 0; i < schemes.length; i++) {
    for (let k = i + 1; k < schemes.length; k++) pairs.push(pairReadout(schemes[i], schemes[k]));
  }

  const share = 1 / schemes.length;
  const reach = new Map<number, number[]>();
  for (const s of schemes) {
    for (const [stock, w] of weightsOf(s)) {
      const list = reach.get(stock) ?? [];
      list.push(w);
      reach.set(stock, list);
    }
  }

  const duplicated = [...reach.entries()]
    .filter(([, ws]) => ws.length > 1)
    .map(([stock, ws]) => ({
      stock,
      inSchemes: ws.length,
      combinedPct: ws.reduce((t, w) => t + w, 0) * share,
    }))
    .sort((x, y) => y.combinedPct - x.combinedPct);

  // Top exposure considers every stock, not only duplicated ones: a single
  // fund with a 12% position is a real concentration even with no overlap.
  let topExposure: PortfolioReadout['topExposure'] = null;
  for (const [stock, ws] of reach) {
    const pct = ws.reduce((t, w) => t + w, 0) * share;
    if (!topExposure || pct > topExposure.pct) {
      topExposure = {stock, pct, inSchemes: ws.length};
    }
  }

  return {
    schemes,
    avgJaccard: pairs.length ? pairs.reduce((t, p) => t + p.jaccard, 0) / pairs.length : 0,
    worstPair: pairs.length ? pairs.reduce((w, p) => (p.weightedOverlap > w.weightedOverlap ? p : w)) : null,
    duplicated,
    topExposure,
  };
}

/** How many of `schemes` hold each stock. Used for the four-banks fact. */
export function holdCounts(schemes: DatasetScheme[]): Map<number, number> {
  const counts = new Map<number, number>();
  for (const s of schemes) {
    for (const stock of namesOf(s)) counts.set(stock, (counts.get(stock) ?? 0) + 1);
  }
  return counts;
}

export const byId = (d: EquityDataset, id: string) => d.schemes.find((s) => s.id === id);
