"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation } from "@tanstack/react-query";
import { runArise, getSelectedSchemes } from "@/lib/api-client";
import { PageHeader, DemoBadge, DataPanel, StatusChip } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { formatINR, formatLabel, formatProbability, formatProbabilityRange } from "@/lib/utils";
import type { AriseResponse, CalibrationRow, RegimeStress } from "@/lib/api-types";

const LOADING_MESSAGES = [
  "Simulating 1,000 lifetimes…",
  "Drawing each lifetime's own expected return…",
  "Replaying the plan with clustered crash years…",
  "Comparing withdrawal strategies…",
];

export default function AriseAnalysisPage() {
  const { data: selected, isLoading: selLoading } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });
  const schemeCount = selected?.selected_scheme_ids?.length ?? 0;

  const mutation = useMutation({ mutationFn: () => runArise({ n_simulations: 1000 }) });

  if (selLoading) return <LoadingState label="Loading your selection…" />;

  if (schemeCount < 1) {
    return (
      <div className="space-y-8">
        <PageHeader
          eyebrow="ARISE engine"
          title="Retirement odds & paths"
          description="A Monte Carlo projection of whether your money outlasts you."
        />
        <div className="ticked mx-auto max-w-xl space-y-4 border border-dashed border-line bg-bg p-12 text-center">
          <p className="label text-accent-text">Schemes required</p>
          <p className="text-step-0 leading-relaxed text-text-secondary">
            Add at least one scheme to your portfolio before running a retirement projection.
          </p>
          <Link href="/schemes" className="btn btn-primary">
            Select schemes
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <PageHeader
        eyebrow="ARISE engine · v4"
        title="Retirement odds & paths"
        description="Not one guess but a distribution: a thousand simulated lifetimes, each with its own markets, inflation, lifespan and expected return."
        action={<DemoBadge />}
      />

      {!mutation.data && !mutation.isPending && (
        <section className="ink-panel ticked grid gap-6 p-8 md:grid-cols-[1fr_auto] md:items-center">
          <div className="space-y-2">
            <p className="label text-accent-text">Ready</p>
            <p className="max-w-[60ch] text-step-1 leading-relaxed text-text">
              Runs your actual portfolio at your actual weights — plus a stress test in which crash years arrive
              together, as they did in 2000–03 and 2008.
            </p>
          </div>
          <button
            type="button"
            onClick={() => mutation.mutate()}
            className="btn border-[var(--ink-accent)] bg-[var(--ink-accent)] font-semibold text-[var(--ink)] hover:!bg-[var(--ink-text)]"
          >
            Run my retirement projection
          </button>
        </section>
      )}

      {mutation.isError && <QueryError error={mutation.error} />}
      {mutation.isPending && <Running />}
      {mutation.data && <AriseResults data={mutation.data} />}
    </div>
  );
}

function Running() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % LOADING_MESSAGES.length), 1400);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="ticked flex min-h-[280px] flex-col items-center justify-center gap-5 border border-line bg-bg p-12">
      <span className="h-9 w-9 animate-spin rounded-full border-2 border-line border-t-accent motion-reduce:animate-none" />
      <p className="font-mono text-step--1 text-text-secondary" aria-live="polite">
        {LOADING_MESSAGES[i]}
      </p>
    </div>
  );
}

function AriseResults({ data }: { data: AriseResponse }) {
  // Headline the ESTIMATE, never the raw share of surviving paths: 1000/1000
  // printed as "100%" claims a certainty no simulation can give.
  const estimate = data.success_rate_estimate ?? data.adaptive_success_rate;
  const survived = Math.round(data.adaptive_success_rate * data.n_simulations);
  const tone = estimate >= 0.75 ? "ok" : estimate >= 0.5 ? "warn" : "err";
  const verdict = estimate >= 0.75 ? "On track" : estimate >= 0.5 ? "At risk" : "Off track";
  const chartData = data.median_withdrawal_path.map((p) => ({ age: p.age, withdrawal: p.withdrawal }));
  const stress = data.regime_stress;

  return (
    <div className="enter space-y-10">
      {/* ── Headline + path ─────────────────────────────────────────────── */}
      <div className="grid items-stretch gap-6 lg:grid-cols-12">
        <section className="ink-panel ticked flex flex-col justify-between gap-8 p-7 lg:col-span-5">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <span className="label">Chance your plan succeeds</span>
              <StatusChip tone={tone}>{verdict}</StatusChip>
            </div>
            <p data-numeric className="font-display text-display-2 font-semibold leading-none text-accent-text">
              {formatProbability(estimate)}
            </p>
            {data.success_rate_low != null && data.success_rate_high != null && (
              <p className="font-mono text-step--1 text-text-secondary">
                95% range {formatProbabilityRange(data.success_rate_low, data.success_rate_high)}
              </p>
            )}
            <p className="max-w-[46ch] text-step--1 leading-relaxed text-text-secondary">
              The money lasts as long as you do, with your lifespan drawn from a mortality curve rather than fixed
              at {data.life_expectancy}. {survived.toLocaleString("en-IN")} of{" "}
              {data.n_simulations.toLocaleString("en-IN")} simulated lifetimes made it.
            </p>
          </div>
          <dl className="grid grid-cols-3 gap-4 border-t border-line pt-5">
            {[
              ["Now", data.current_age],
              ["Retire", data.retire_age],
              ["Plan to", data.life_expectancy],
            ].map(([k, v]) => (
              <div key={k as string}>
                <dt className="label">{k}</dt>
                <dd data-numeric className="mt-1 text-step-3 font-medium text-text">
                  {v}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="ticked flex min-h-[420px] flex-col border border-line bg-bg p-6 lg:col-span-7">
          <div className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-display text-step-2 font-semibold text-text">Withdrawal path</h2>
            <span className="font-mono text-step--2 text-text-muted">median lifetime · today&apos;s money</span>
          </div>
          <div className="min-h-[300px] flex-1 font-mono text-step--2">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 8, left: -8, bottom: 0 }}>
                <defs>
                  <linearGradient id="wd" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.22} />
                    <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="age" stroke="var(--text-muted)" tickLine={false} axisLine={false} />
                <YAxis
                  stroke="var(--text-muted)"
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => formatINR(v)}
                  width={60}
                />
                <Tooltip
                  formatter={(value) => [formatINR(Number(value), { compact: true }), "Withdrawal"]}
                  labelFormatter={(label) => `Age ${label}`}
                  contentStyle={{
                    background: "var(--bg)",
                    border: "1px solid var(--line)",
                    borderRadius: 0,
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                  }}
                />
                {/* The path starts at the first withdrawal year; only mark
                    retirement when it actually falls inside the chart. */}
                {chartData.length > 0 && data.retire_age > chartData[0].age && (
                <ReferenceLine
                  x={data.retire_age}
                  stroke="var(--text)"
                  strokeDasharray="3 3"
                  label={{ value: "retire", position: "insideTopRight", fill: "var(--text-muted)", fontSize: 10 }}
                />
                )}
                <Area type="monotone" dataKey="withdrawal" stroke="var(--accent)" strokeWidth={2} fill="url(#wd)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      {/* ── Stress test ─────────────────────────────────────────────────── */}
      {stress && <StressTest main={data} stress={stress} />}

      {/* ── Terminal wealth ─────────────────────────────────────────────── */}
      <section className="ticked border border-line bg-bg p-6">
        <div className="mb-8 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-display text-step-2 font-semibold text-text">What you could leave behind</h2>
          <span className="font-mono text-step--2 text-text-muted">wealth at age {data.life_expectancy}</span>
        </div>
        <TerminalWealthSpread data={data} />
      </section>

      {/* ── Strategy + assumptions ──────────────────────────────────────── */}
      <div className="grid gap-6 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <DataPanel label="Withdrawal strategies, same markets" note="common random numbers">
            <table className="w-full border-collapse text-left text-step--1">
              <thead>
                <tr className="border-b border-line">
                  <th className="p-4 text-left font-normal"><span className="label">Strategy</span></th>
                  <th className="p-4 text-right font-normal"><span className="label">Success</span></th>
                  <th className="p-4 text-right font-normal"><span className="label">Income swings</span></th>
                </tr>
              </thead>
              <tbody>
                {data.strategy_leaderboard.map((s) => {
                  const primary = s.strategy === data.primary_strategy;
                  return (
                    <tr key={s.strategy} className={`border-b border-line last:border-0 ${primary ? "bg-bg-subtle" : ""}`}>
                      <td className="p-4 font-medium text-text">
                        {formatLabel(s.strategy)}
                        {primary && <span className="label ml-2 text-accent-text">recommended</span>}
                      </td>
                      <td data-numeric className="p-4 text-right text-text">
                        {formatProbability(s.success_estimate ?? s.success_rate)}
                        {s.success_low != null && s.success_high != null && (
                          <span className="block font-mono text-step--2 text-text-muted">
                            {formatProbabilityRange(s.success_low, s.success_high)}
                          </span>
                        )}
                      </td>
                      <td data-numeric className="p-4 text-right text-text-secondary">
                        {(s.income_volatility * 100).toFixed(0)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </DataPanel>
        </div>

        <section className="ticked space-y-4 border border-line bg-bg p-6 lg:col-span-4">
          <div className="flex items-baseline justify-between">
            <h2 className="label">What we assumed</h2>
            <Link href="/profile" className="font-mono text-step--2 text-accent-text underline-offset-4 hover:underline">
              Adjust
            </Link>
          </div>
          <dl className="space-y-3 font-mono text-step--1">
            <Row k="Monthly SIP" v={formatINR(data.assumptions.monthly_sip, { compact: false })} />
            {data.assumptions.monthly_sip_is_estimated && (
              <p className="text-step--2 text-accent-text">Estimated — set your real SIP in your profile.</p>
            )}
            <Row k="Expected return used" v={`${(data.portfolio_cagr_used * 100).toFixed(2)}%`} strong />
            <Row k="Raw historical average" v={`${(data.shrinkage_diagnostics.portfolio_cagr_raw * 100).toFixed(2)}%`} />
            {data.volatility_raw != null && data.volatility_prior != null && (
              <Row
                k="Volatility used"
                v={`${(data.portfolio_volatility_used * 100).toFixed(1)}%`}
                strong
              />
            )}
            {data.volatility_raw != null && (
              <Row k="Your funds' own (3y)" v={`${(data.volatility_raw * 100).toFixed(1)}%`} />
            )}
            {data.valuation_adjustment != null && data.valuation_adjustment !== 0 && (
              <Row k="Valuation adjustment" v={`${(data.valuation_adjustment * 100).toFixed(2)} pts/yr`} />
            )}
            <Row k="Recommended strategy" v={formatLabel(data.primary_strategy)} />
            {data.tax_saving_pct > 0 && <Row k="Tax saved vs fixed" v={`${data.tax_saving_pct.toFixed(1)}%`} />}
          </dl>
          <p className="border-t border-line pt-3 text-step--2 leading-relaxed text-text-muted">
            The expected return is shrunk toward a long-run prior instead of extrapolating your funds&apos; recent
            history.
          </p>
        </section>
      </div>

      {/* ── How accurate is ARISE ───────────────────────────────────────── */}
      {data.calibration && <Calibration data={data} />}

      {/* ── How the model was chosen ────────────────────────────────────── */}
      {stress && <ModelEvidence stress={stress} />}

      {data.milevsky_ruin_prob !== null && (
        <section className="ticked border border-line bg-bg-subtle p-6">
          <h2 className="font-display text-step-1 font-semibold text-text">An independent cross-check</h2>
          <p className="mt-2 max-w-[90ch] text-step--1 leading-relaxed text-text-secondary">
            A closed-form academic model (Milevsky) puts ruin at{" "}
            <span data-numeric className="text-text">{(data.milevsky_ruin_prob * 100).toFixed(0)}%</span>, against
            this simulation&apos;s{" "}
            <span data-numeric className="text-text">
              {data.mc_ruin_prob_constant !== null ? (data.mc_ruin_prob_constant * 100).toFixed(0) : "—"}%
            </span>
            . They are not meant to agree: Milevsky assumes a fixed corpus at retirement with no years of saving
            ahead of it. A sanity check, not a second opinion.
          </p>
        </section>
      )}
    </div>
  );
}

function Row({ k, v, strong }: { k: string; v: React.ReactNode; strong?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-text-secondary">{k}</dt>
      <dd data-numeric className={strong ? "font-semibold text-text" : "text-text"}>
        {v}
      </dd>
    </div>
  );
}

/** Two horizontal bars with their 95% ranges on one 0-100 scale. */
function StressTest({ main, stress }: { main: AriseResponse; stress: RegimeStress }) {
  const rows = [
    {
      label: "Main projection",
      note: "Student-t returns",
      est: main.success_rate_estimate ?? main.adaptive_success_rate,
      lo: main.success_rate_low ?? 0,
      hi: main.success_rate_high ?? 1,
      ink: true,
    },
    {
      label: "Stress test",
      note: "crash years cluster",
      est: stress.success_estimate,
      lo: stress.success_low,
      hi: stress.success_high,
      ink: false,
    },
  ];
  const gap = (rows[0].est - rows[1].est) * 100;
  const calm = stress.regimes.find((r) => r.name === "calm");
  const turb = stress.regimes.find((r) => r.name === "turbulent");

  return (
    <section className="ticked grid gap-8 border border-line bg-bg p-6 lg:grid-cols-12">
      <div className="space-y-3 lg:col-span-5">
        <span className="ink-rule" aria-hidden="true" />
        <p className="label text-accent-text">Regime stress test · machine-learned</p>
        <h2 className="font-display text-step-3 font-semibold leading-tight text-text">
          {Math.abs(gap) < 1
            ? "Clustered crashes barely move your odds."
            : gap > 0
              ? `If crashes cluster, your odds fall ${gap.toFixed(0)} points.`
              : `Clustered crashes would not lower your odds.`}
        </h2>
        <p className="text-step--1 leading-relaxed text-text-secondary">
          A hidden Markov model trained on 29 years of Sensex returns finds two market states — calm (
          {calm ? `${(calm.annual_vol * 100).toFixed(0)}%` : "—"} volatility, typically lasting{" "}
          {calm ? Math.round(calm.expected_months / 12) : "—"} years) and turbulent (
          {turb ? `${(turb.annual_vol * 100).toFixed(0)}%` : "—"}, about {turb ? Math.round(turb.expected_months / 12) : "—"}{" "}
          years). Same plan, same expected return; only the order of good and bad years changes.
        </p>
      </div>
      <div className="space-y-6 self-center lg:col-span-7">
        {rows.map((r) => (
          <div key={r.label}>
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <span className="text-step--1 font-medium text-text">
                {r.label} <span className="font-mono text-step--2 text-text-muted">· {r.note}</span>
              </span>
              <span data-numeric className="text-step-1 font-medium text-text">
                {formatProbability(r.est)}
              </span>
            </div>
            <div className="relative h-3 bg-bg-subtle" aria-hidden="true">
              <div
                className="absolute inset-y-0 left-0"
                style={{ width: `${r.est * 100}%`, background: r.ink ? "var(--mark)" : "var(--accent)" }}
              />
              <div
                className="absolute -inset-y-1 border-x border-text"
                style={{ left: `${r.lo * 100}%`, width: `${Math.max(0.4, (r.hi - r.lo) * 100)}%` }}
              />
            </div>
            <p className="mt-1 font-mono text-step--2 text-text-muted">
              95% range {formatProbabilityRange(r.lo, r.hi)}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

const MODEL_NAMES: Record<string, string> = {
  student_t_iid: "Student-t (used)",
  gaussian_hmm_1: "Normal distribution",
  gaussian_hmm_2: "2-regime HMM, normal",
  t_hmm_2: "2-regime HMM, Student-t",
};

function ModelEvidence({ stress }: { stress: RegimeStress }) {
  const results = Object.entries(stress.validation.results).sort((a, b) => b[1] - a[1]);
  const best = results[0]?.[1] ?? 0;
  const sb = stress.shrinkage_backtest;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <DataPanel label="Why the main model is Student-t" note="higher is better">
        <table className="w-full border-collapse text-step--1">
          <tbody>
            {results.map(([k, v]) => (
              <tr key={k} className="border-b border-line last:border-0">
                <td className="p-4 text-text">{MODEL_NAMES[k] ?? k}</td>
                <td data-numeric className="p-4 text-right text-text">
                  {v.toFixed(1)}
                  <span className="ml-2 font-mono text-step--2 text-text-muted">
                    {v === best ? "best" : `−${(best - v).toFixed(1)}`}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t border-line px-5 py-3 text-step--2 leading-relaxed text-text-muted">
          {stress.validation.method}. Regime models did not forecast better, so they power the stress test only.
        </p>
      </DataPanel>

      {sb && (
        <DataPanel label="How much to trust past returns" note={`${sb.n_forecasts} real forecasts`}>
          <table className="w-full border-collapse text-step--1">
            <tbody>
              {Object.entries(sb.rmse_by_tau)
                .filter(([k]) => k !== "prior_only")
                .map(([k, v]) => (
                  <tr key={k} className={`border-b border-line last:border-0 ${Number(k) === sb.best_tau ? "bg-bg-subtle" : ""}`}>
                    <td className="p-4 text-text">
                      τ = {k} years{" "}
                      {Number(k) === sb.best_tau && <span className="label ml-1 text-accent-text">lowest error · used</span>}
                    </td>
                    <td data-numeric className="p-4 text-right text-text">
                      {(v * 100).toFixed(2)} pts
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          <p className="border-t border-line px-5 py-3 text-step--2 leading-relaxed text-text-muted">
            Each fund&apos;s next-3-year return forecast from its own history, shrunk toward 11% with weight n/(n+τ).
            Past returns correlated {sb.corr_trailing_vs_forward.toFixed(2)} with what came next — history alone is
            a poor guide.
          </p>
        </DataPanel>
      )}
    </div>
  );
}

function TerminalWealthSpread({ data }: { data: AriseResponse }) {
  // Percentiles in columns, not positioned on one axis: depleted paths put the
  // low percentiles near zero while the 90th runs to crores, so on a single
  // linear scale their labels pile on top of each other.
  const points = [
    { key: "p10", label: "10th", value: data.terminal_p10, note: "bad markets" },
    { key: "p25", label: "25th", value: data.terminal_p25, note: "" },
    { key: "p50", label: "Median", value: data.terminal_p50, note: "typical" },
    { key: "p75", label: "75th", value: data.terminal_p75, note: "" },
    { key: "p90", label: "90th", value: data.terminal_p90, note: "good markets" },
  ];
  const max = Math.max(data.terminal_p90, 1);
  return (
    <div className="space-y-5">
      <div className="relative h-2 bg-bg-subtle" aria-hidden="true">
        <div
          className="absolute inset-y-0 bg-[var(--mark)] opacity-80"
          style={{ left: `${(data.terminal_p25 / max) * 100}%`, width: `${((data.terminal_p75 - data.terminal_p25) / max) * 100}%` }}
        />
        <div className="absolute -inset-y-1.5 w-0.5 bg-accent" style={{ left: `${(data.terminal_p50 / max) * 100}%` }} />
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-5">
        {points.map((p) => (
          <div key={p.key} className={p.key === "p50" ? "border-l-2 border-accent pl-3" : "border-l border-line pl-3"}>
            <dt className="label">{p.label}</dt>
            <dd
              data-numeric
              className={`mt-1 text-step-1 ${p.key === "p50" ? "font-semibold text-accent-text" : "text-text"}`}
            >
              {formatINR(p.value)}
            </dd>
            {p.note && <dd className="font-mono text-step--2 text-text-muted">{p.note}</dd>}
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Out-of-sample calibration: did the forecast ranges contain reality as
 *  often as they claimed? Before/after the v5 changes, on real funds. */
function Calibration({ data }: { data: AriseResponse }) {
  const c = data.calibration!;
  const v = data.valuation_context;
  const rows: CalibrationRow[] = [c.before, c.after, c.after_with_valuation];
  return (
    <section className="ticked grid gap-8 border border-line bg-bg p-6 lg:grid-cols-12">
      <div className="space-y-3 lg:col-span-4">
        <span className="ink-rule" aria-hidden="true" />
        <p className="label text-accent-text">How accurate is this?</p>
        <h2 className="font-display text-step-3 font-semibold leading-tight text-text">
          Its ranges now hold reality as often as they claim.
        </h2>
        <p className="text-step--1 leading-relaxed text-text-secondary">
          We replayed ARISE on every Indian equity fund since 2016 — forecasting three years ahead with only the
          data available at the time — and checked where reality landed. The old model&apos;s 80% range caught
          only {(c.before.coverage_80 * 100).toFixed(0)}% of outcomes: it was overconfident. Two fixes the data
          supported brought it to {(c.after.coverage_80 * 100).toFixed(0)}%.
        </p>
        <p className="font-mono text-step--2 text-text-muted">{c.method}</p>
      </div>
      <div className="space-y-4 lg:col-span-8">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-step--1">
            <thead>
              <tr className="border-b border-line">
                <th className="p-3 text-left font-normal"><span className="label">Model</span></th>
                <th className="p-3 text-right font-normal"><span className="label">80% range holds</span></th>
                <th className="p-3 text-right font-normal"><span className="label">50% range holds</span></th>
                <th className="p-3 text-right font-normal"><span className="label">CRPS</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.label} className={`border-b border-line last:border-0 ${i === 2 ? "bg-bg-subtle" : ""}`}>
                  <td className="p-3 text-text">{r.label}{i === 2 && <span className="label ml-2 text-accent-text">used</span>}</td>
                  <td data-numeric className={`p-3 text-right ${Math.abs(r.coverage_80 - 0.8) < 0.05 ? "text-ok" : "text-accent-text"}`}>
                    {(r.coverage_80 * 100).toFixed(1)}%
                  </td>
                  <td data-numeric className={`p-3 text-right ${Math.abs(r.coverage_50 - 0.5) < 0.05 ? "text-ok" : "text-accent-text"}`}>
                    {(r.coverage_50 * 100).toFixed(1)}%
                  </td>
                  <td data-numeric className="p-3 text-right text-text-secondary">{r.crps.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="grid gap-3 text-step--2 leading-relaxed text-text-secondary sm:grid-cols-2">
          <li className="border-l-2 border-accent pl-3">
            <span className="font-medium text-text">No assumed mean reversion.</span> 29 years of Sensex returns can&apos;t
            tell it from noise, and assuming it made multi-year ranges too narrow.
          </li>
          <li className="border-l-2 border-accent pl-3">
            <span className="font-medium text-text">Volatility blended with the long run.</span> A calm three years
            understates risk; your funds&apos; own {data.volatility_raw != null ? `${(data.volatility_raw * 100).toFixed(1)}%` : ""} is
            averaged with the 29-year market level.
          </li>
        </ul>
        {v && (
          <p className="border-t border-line pt-4 text-step--2 leading-relaxed text-text-muted">
            <span className="font-medium text-text-secondary">Valuation view.</span> Nifty&apos;s dividend yield (
            {v.dividend_yield.toFixed(2)}%) points to {(v.forecast_5y * 100).toFixed(1)}% a year over five years against a
            {" "}{(v.hist_mean * 100).toFixed(1)}% average. Out of sample it beat the average (R²{" "}
            {v.oos["5y"]?.oos_r2.toFixed(2)}), but 27 years hold only a handful of independent five-year windows, so it
            gets {Math.round(v.evidence_weight * 100)}% weight, for the first five years only — a{" "}
            {((data.valuation_adjustment ?? 0) * 100).toFixed(2)}-point change a year to your projection.
          </p>
        )}
      </div>
    </section>
  );
}
