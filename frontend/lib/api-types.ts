/**
 * TypeScript types mirroring the REAL, verified FastAPI backend response
 * shapes exactly (backend/schemas/*.py). Every field here was checked
 * against the actual Pydantic models, not guessed -- see the backend repo's
 * schemas/analysis.py, schemas/schemes.py, schemas/profile.py.
 */

// ── Profile ──────────────────────────────────────────────────────────────
export type InvestmentGoal = "growth" | "income" | "safety" | "tax_saving";
export type RiskAppetite =
  | "conservative"
  | "moderate"
  | "moderately_high"
  | "aggressive";

export interface ProfileCreate {
  full_name: string;
  date_of_birth: string; // YYYY-MM-DD
  occupation: string;
  monthly_income: number;
  total_corpus: number;
  investment_amount: number;
  goal: InvestmentGoal;
  risk_appetite: RiskAppetite;
  target_retirement_age: number;
  monthly_expenses_in_retirement: number;
  monthly_sip?: number;
  sip_step_up_pct?: number;
}

export interface ProfileResponse extends ProfileCreate {
  id: string;
  user_id: string;
  age: number;
  years_to_retirement: number;
  is_complete: boolean;
}

// ── Schemes ──────────────────────────────────────────────────────────────
export interface SchemeInfo {
  id: string;
  name: string;
  category: string | null;
  instrument_type: "equity" | "debt";
  risk_rating: string | null;
  risk_score_1_to_6: number | null;
  riskometer: string | null;
  aum_cr: number | null;
  nav_regular_growth: number | null;
  expense_ratio_regular: number | null;
  min_investment: number | null;
  suitable_for: string[];
  benchmark: string | null;
  holdings_count: number;
  subtier: "Core" | "Growth" | "Aggressive" | null;
  risk_flag: string | null;
  risk_flag_level: "match" | "mild" | "strong";
  upstox_purchasable: boolean | null;
  upstox_min_investment: number | null;
}

export interface SchemeDetail extends SchemeInfo {
  fund_manager: string | null;
  investment_philosophy: string | null;
  inception_date: string | null;
  entry_load: string | null;
  exit_load: string | null;
  portfolio_turnover: number | null;
  std_deviation: number | null;
  beta: number | null;
  sharpe_ratio: number | null;
  ytm: string | null;
  modified_duration: string | null;
  macaulay_duration: string | null;
  avg_maturity: string | null;
  // weight is genuinely nullable: at least one real scheme has a combined
  // sector category ("Pharmaceuticals & Biotechnology / Electrical
  // Equipment") with no single clean weight to report.
  sector_allocation: { sector: string; weight: number | null }[];
  sip_returns: Record<
    string,
    {
      amount_invested: number;
      market_value: number;
      scheme_return_pct: number;
      // Genuinely nullable, not a data-quality gap: benchmark index history
      // (e.g. Nifty) often doesn't extend as far back as a fund's own real
      // returns for "since inception" / 25yr / 30yr periods.
      benchmark_return_pct: number | null;
      add_benchmark_return_pct: number | null;
    }
  >;
  top_holdings: { name: string; weight: number }[];
}

export interface FilterResult {
  eligible_count: number;
  total_schemes: number;
  eligible_schemes: SchemeInfo[];
  filtered_out: { scheme_id: string; scheme_name: string; reasons: string[] }[];
  filter_summary: {
    by_age: number;
    by_income: number;
    by_investment_amount: number;
    by_risk: number;
    by_goal: number;
    already_held: number;
    risk_flagged: number;
    goal_flagged: number;
    total_filtered: number;
    closed_to_purchase: number;
  };
}

export interface SelectedSchemes {
  id: string;
  user_id: string;
  selected_scheme_ids: string[];
  weights: Record<string, number>;
  schemes: SchemeInfo[];
}

/**
 * The REAL response shape of POST /schemes/select -- confirmed against the
 * backend's actual SelectSchemesResponse Pydantic model, not assumed. This
 * is genuinely NOT the same shape as SelectedSchemes (GET /schemes/selected)
 * -- notably, no `selected_scheme_ids` field. Caching this mutation's raw
 * response directly into the ["selected-schemes"] query slot (as an earlier
 * version of this code did) silently wiped `selected_scheme_ids` from the
 * cache on every successful selection, which is why only the single most
 * recently clicked scheme ever appeared to stay selected -- every click
 * after the first computed its next-list from an `undefined` base.
 */
export interface SelectSchemesResult {
  selected_count: number;
  schemes: SchemeInfo[];
  weights: Record<string, number>;
  message: string;
}

// ── Overlap ──────────────────────────────────────────────────────────────
export interface OverlapNode {
  id: string;
  name: string;
}
export interface OverlapEdge {
  source_id: string;
  target_id: string;
  jaccard_score: number;
  weighted_overlap: number;
  shared_stocks: number;
  shared_stock_names: string[];
}
export interface HeatmapCell {
  row_scheme_id: string;
  col_scheme_id: string;
  jaccard_score: number;
}
export interface OverlapResponse {
  scheme_ids: string[];
  nodes: OverlapNode[];
  edges: OverlapEdge[];
  heatmap: HeatmapCell[];
  overlap_threshold: number;
  avg_pairwise_jaccard: number;
  most_overlapping_pair: [string, string] | null;
  least_overlapping_pair: [string, string] | null;
  scheme_overlap_summary: Record<string, unknown>[];
}

// ── PLOS ─────────────────────────────────────────────────────────────────
export interface PlosLayer {
  layer_id: string;
  description: string;
  raw_score: number;
  weight: number;
  contribution: number;
  data_source: string;
}
export interface PairPlos {
  scheme_a_id: string;
  scheme_b_id: string;
  scheme_a_name: string;
  scheme_b_name: string;
  plos_score: number;
  real_correlation: number | null;
  layers: PlosLayer[];
  interpretation: string;
  data_quality: string;
}
export interface Top5Recommendation {
  rank: number;
  scheme_id: string;
  scheme_name: string;
  avg_plos_with_others: number;
}
export interface PlosResponse {
  scheme_ids: string[];
  pair_scores: PairPlos[];
  portfolio_plos: number;
  diversification_score: number;
  interpretation: string;
  top_5_recommendation: Top5Recommendation[];
  most_overlapping_pair: [string, string] | null;
  least_overlapping_pair: [string, string] | null;
  data_source: string;
}

export interface CompareResponse {
  candidate: {
    scheme_ids: string[];
    weights: Record<string, number>;
    overlap: OverlapResponse;
    plos: PlosResponse;
  };
  current: {
    scheme_ids: string[];
    weights: Record<string, number>;
    overlap: OverlapResponse;
    plos: PlosResponse;
  } | null;
}

// ── Risk ─────────────────────────────────────────────────────────────────
export interface SchemeRiskMetrics {
  scheme_id: string;
  scheme_name: string;
  annualized_return: number;
  annualized_volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
  beta_vs_nifty: number | null;
  var_95_parametric: number;
  cvar_95_parametric: number;
  var_95_historical: number;
  cvar_95_historical: number;
  var_99_parametric: number;
  cvar_99_parametric: number;
  mc_p10_return: number;
  mc_p25_return: number;
  mc_p50_return: number;
  mc_p75_return: number;
  mc_p90_return: number;
  mc_ruin_probability: number;
  data_points: number;
  is_synthetic: boolean;
}
export interface RiskResponse {
  scheme_ids: string[];
  scheme_metrics: SchemeRiskMetrics[];
  portfolio_annualized_return: number;
  portfolio_volatility: number;
  portfolio_sharpe: number;
  portfolio_max_drawdown: number;
  portfolio_var_95: number;
  portfolio_cvar_95: number;
  correlation_matrix: number[][];
  correlation_labels: string[];
  covariance_matrix: number[][];
  weighted_avg_vol: number;
  diversification_ratio: number;
  data_source: string;
  n_observations: number;

  // ── Added by the backend's risk-engine upgrade. All optional so an older
  //    backend (or a demo-mode payload) still type-checks. ──────────────────
  /** "investor" = your real allocation; "equal" = fallback. The dashboard used
   *  to show equal-weight risk for a portfolio nobody held. */
  weighting_scheme?: "investor" | "equal";
  weights?: Record<string, number>;
  /** Named so nobody has to guess what portfolio beta is relative to. */
  portfolio_benchmark?: string | null;
  portfolio_sortino?: number;
  portfolio_calmar?: number;
  portfolio_beta?: number | null;
  portfolio_alpha?: number | null;
  portfolio_tracking_error?: number | null;
  /** SEBI mandates this disclosure for equity schemes. */
  portfolio_information_ratio?: number | null;
  portfolio_var_95_cornish_fisher?: number;
  /** Kupiec proportion-of-failures test: does the VaR actually hold up? */
  portfolio_var_backtest_95?: VarBacktest;
  /** Conservatism, reported rather than folded into the headline. */
  expected_return_unbiased?: number;
  conservatism_haircut?: number;
  expected_return_after_haircut?: number;
  /** Hierarchical Risk Parity reference weights. Not a recommendation. */
  suggested_allocation?: SuggestedAllocation;
}

export interface VarBacktest {
  observations: number;
  breaches: number;
  expected_breaches: number;
  observed_rate: number;
  expected_rate: number;
  lr_statistic: number | null;
  p_value: number | null;
  /** null when the sample is too short for the test to mean anything. */
  passed: boolean | null;
}

export interface SuggestedAllocation {
  available: boolean;
  reason?: string;
  method?: string;
  weights: Record<string, number>;
  basis?: string;
  caveats?: string[];
  current_weights?: Record<string, number>;
  delta?: Record<string, number>;
  /** Present when risk parity collapses onto one low-volatility holding. */
  concentration_warning?: string;
}

export interface Activeness {
  verdict: "possible_closet_index" | "active" | "undetermined";
  tracking_error: number | null;
  r_squared: number | null;
  expense_ratio: number | null;
  cost_per_unit_active_risk: number | null;
  reasons: string[];
  caveats: string[];
}

// ── ARISE ────────────────────────────────────────────────────────────────
export interface AriseRequest {
  n_simulations?: number;
  life_expectancy?: number;
  slab_rate?: number;
  strategy?: "robotic" | "adaptive" | "vpw";
  step_up?: number;
  monthly_sip?: number;
  spending_model?: "constant_real" | "blanchett_smile";
  glide_mode?: "declining" | "rising" | "static";
  cape?: number;
}
export interface AriseResponse {
  n_simulations: number;
  retire_age: number;
  life_expectancy: number;
  current_age: number;
  data_source: string;
  n_years_history_used: number;
  portfolio_cagr_used: number;
  portfolio_volatility_used: number;
  risk_free_rate_proxy_used: number;
  robotic_success_rate: number;
  adaptive_success_rate: number;
  terminal_p10: number;
  terminal_p25: number;
  terminal_p50: number;
  terminal_p75: number;
  terminal_p90: number;
  robotic_avg_tax: number;
  adaptive_avg_tax: number;
  tax_saving_pct: number;
  avg_gk_cuts_per_path: number;
  avg_gk_raises_per_path: number;
  avg_vol_derisk_years: number;
  median_withdrawal_path: { year: number; age: number; withdrawal: number }[];
  scheme_ids: string[];
  weights: Record<string, number>;
  data_quality_warning: string | null;
  engine_version: string;
  primary_strategy: string;
  spending_model_used: string;
  glide_mode_used: string;
  cape_swr_used: number | null;
  avg_ratchet_raises_per_path: number;
  income_volatility: number;
  milevsky_ruin_prob: number | null;
  mc_ruin_prob_constant: number | null;
  milevsky_mc_abs_gap: number | null;
  strategy_leaderboard: {
    strategy: string;
    /** Raw share of paths that survived. A sample, not a probability. */
    success_rate: number;
    /** Jeffreys posterior mean and 95% credible interval -- headline these. */
    success_estimate?: number;
    success_low?: number;
    success_high?: number;
    income_volatility: number;
  }[];
  /** Primary strategy's success as an estimate: never exactly 0 or 1. */
  success_rate_estimate?: number;
  success_rate_low?: number;
  success_rate_high?: number;
  robotic_success_estimate?: number;
  /** sd of each path's expected-return draw (estimation risk); 0 = off. */
  parameter_uncertainty_sd?: number;
  assumptions: {
    current_portfolio_value_source: string;
    monthly_sip: number;
    monthly_sip_source: string;
    monthly_sip_is_estimated: boolean;
    step_up: number;
    step_up_source: string;
  };
  shrinkage_diagnostics: {
    portfolio_cagr_raw: number;
    portfolio_cagr_used: number;
    prior_cagr_used: number;
    sample_weight_cagr: number;
    return_capped: boolean;
  };
}

// ── Auth ─────────────────────────────────────────────────────────────────
export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}
export interface MeResponse {
  user_id: string;
  email: string;
  role: string;
  is_active: boolean;
}

// ── Dashboard ────────────────────────────────────────────────────────────
export interface DashboardResponse {
  investor: {
    name: string | null;
    age: number | null;
    years_to_retirement: number | null;
    goal: string | null;
    risk_appetite: string | null;
  };
  portfolio: {
    scheme_count: number;
    equity_schemes: number;
    debt_schemes: number;
    invested_amount: number;
    current_value: number;
    holdings: Record<string, unknown>[];
  };
  market: {
    is_open: boolean;
    data_delayed: boolean;
    top_holdings: Record<string, unknown>[];
  };
  pnl_available: boolean;
}

export interface ApiError {
  error: string;
  message: string;
  details?: unknown;
  request_id?: string;
}
