import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Compact INR formatting: 4,620,000 -> "₹46.2L", 97,400,000 -> "₹9.74Cr" */
export function formatINR(value: number, opts: { compact?: boolean } = {}): string {
  const { compact = true } = opts;
  if (!compact) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    }).format(value);
  }
  const abs = Math.abs(value);
  if (abs >= 1e7) return `₹${(value / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `₹${(value / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `₹${(value / 1e3).toFixed(1)}K`;
  return `₹${value.toFixed(0)}`;
}

export function formatPct(value: number, decimals = 1): string {
  return `${(value * 100).toFixed(decimals)}%`;
}

/**
 * A simulated probability, for display. Never "100%" and never "0%": a Monte
 * Carlo run can show an outcome is very likely, not certain -- 1,000 survivals
 * out of 1,000 only establish that failure is probably under ~0.3%.
 */
export function probabilityParts(p: number): { prefix: string; value: number } {
  if (p >= 0.995) return { prefix: ">", value: 99 };
  if (p <= 0.005) return { prefix: "<", value: 1 };
  return { prefix: "", value: Math.round(p * 100) };
}

export function formatProbability(p: number): string {
  const { prefix, value } = probabilityParts(p);
  return `${prefix}${value}%`;
}

/** "87–91%" -- the 95% credible range, clamped the same way. */
export function formatProbabilityRange(low: number, high: number): string {
  const lo = probabilityParts(low);
  const hi = probabilityParts(high);
  // Both ends clamp to the same reading (e.g. a near-perfect run): one value.
  if (lo.value === hi.value) return formatProbability(low);
  return `${lo.value}–${hi.value}%`;
}

export function schemeShortName(name: string): string {
  return name.replace(/^Nippon India /, "");
}

/** "very_high" -> "Very High" -- the backend returns some fields (risk_rating,
 * goal, risk_appetite) as lowercase snake_case enum values, not display text. */
export function formatLabel(value: string): string {
  return value
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
