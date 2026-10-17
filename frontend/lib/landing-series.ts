/**
 * The engine-card sparklines, COMPUTED from the backend's own formulas and
 * constants rather than drawn by hand. Each one is a real shape the engine
 * uses, so the illustration cannot misrepresent the model.
 *
 * Constants mirror the backend exactly (verified against source):
 *   plos_engine.LAYER_WEIGHTS          the 10 layer weights, summing to 1.0
 *   risk_engine.MC_T_DF = 5.0          Student-t degrees of freedom
 *   arise_engine.GOMPERTZ_DISPERSION   10.0
 *   arise_engine.MORTALITY_MAX_AGE     105
 *   schemas.analysis life_expectancy   85 (default modal age)
 *   config.ARISE_DEFAULT_SIMULATIONS   1000
 */
import facts from './generated/landing-facts.json';

export const PLOS_LAYER_WEIGHTS = [0.3, 0.1, 0.1, 0.07, 0.07, 0.07, 0.07, 0.05, 0.09, 0.08];
export const STUDENT_T_DF = 5;
export const GOMPERTZ_DISPERSION = 10;
export const MORTALITY_MAX_AGE = 105;
export const DEFAULT_LIFE_EXPECTANCY = 85;
export const ARISE_DEFAULT_SIMULATIONS = 1000;
export const MODEL_CONSERVATISM_HAIRCUT_PP = 1.25;

/** All 190 pairwise weighted overlaps, high to low. */
export const overlapCurve: number[] = facts.overlapCurve;

/** PLOS layer weights, largest first -- return correlation dominates. */
export const plosWeights = [...PLOS_LAYER_WEIGHTS].sort((a, b) => b - a);

/** Student-t(nu=5) density shape over [-4, 4]. Normalisation is irrelevant to
 *  a sparkline, so only the kernel (1 + x^2/nu)^(-(nu+1)/2) is evaluated. */
export const studentT = Array.from({length: 41}, (_, i) => {
  const x = -4 + i * 0.2;
  return Math.pow(1 + (x * x) / STUDENT_T_DF, -(STUDENT_T_DF + 1) / 2);
});

/** Gompertz survival from age 60, the engine's own S(t):
 *    S(t) = exp( e^((x-m)/b) * (1 - e^(t/b)) ) */
export const survival = Array.from({length: MORTALITY_MAX_AGE - 60 + 1}, (_, t) => {
  const x = 60;
  const m = DEFAULT_LIFE_EXPECTANCY;
  const b = GOMPERTZ_DISPERSION;
  return Math.exp(Math.exp((x - m) / b) * (1 - Math.exp(t / b)));
});

export {facts};

/** "31 May 2026" from an ISO date, without timezone drift. */
export function formatAsOf(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${d} ${months[m - 1]} ${y}`;
}

/** "Nippon India Large Cap Fund" -> "Large Cap" */
export const shortScheme = (name: string) =>
  name.replace(/^Nippon India\s+/, '').replace(/\s+Fund$/, '');

/** "HDFC Bank Limited" -> "HDFC Bank" */
export const shortStock = (name: string) =>
  name.replace(/\s+Limited$/, '').replace(/\s+Ltd\.?$/, '');
