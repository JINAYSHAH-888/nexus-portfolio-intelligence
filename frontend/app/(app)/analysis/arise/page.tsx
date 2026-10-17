"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useQuery, useMutation } from "@tanstack/react-query";
import { runArise, getSelectedSchemes } from "@/lib/api-client";
import { PageHeader, DemoBadge } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { motion, AnimatePresence } from "motion/react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer
} from "recharts";
import { AlertTriangle, TrendingUp, HelpCircle } from "lucide-react";
import { formatINR, formatLabel, formatProbability, formatProbabilityRange } from "@/lib/utils";
import type { AriseResponse } from "@/lib/api-types";

const LOADING_MESSAGES = [
  "Simulating 1,000 market scenarios…",
  "Modeling your withdrawal strategy…",
  "Comparing decumulation strategies…",
  "Checking assumptions against real data…",
];

export default function AriseAnalysisPage() {
  const { data: selected, isLoading: selLoading } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });
  const schemeCount = selected?.selected_scheme_ids?.length ?? 0;

  const [loadingMsg, setLoadingMsg] = useState(0);
  const mutation = useMutation({
    mutationFn: () => runArise({ n_simulations: 1000 }),
    onMutate: () => {
      let i = 0;
      const interval = setInterval(() => {
        i = (i + 1) % LOADING_MESSAGES.length;
        setLoadingMsg(i);
      }, 1200);
      return () => clearInterval(interval);
    },
  });

  if (selLoading) return <LoadingState label="Loading your selection…" />;

  if (schemeCount < 1) {
    return (
      <div className="space-y-8">
        <PageHeader
          eyebrow="ARISE Engine"
          title="Retirement Wealth Projection"
          description="Runs a Student-t Monte Carlo simulation across real market volatility."
        />
        <div className="border border-line border-dashed p-12 text-center select-none bg-paper-tint/10 max-w-xl mx-auto space-y-4">
          <AlertTriangle className="w-8 h-8 text-crimson mx-auto" />
          <p className="font-mono text-xs text-crimson uppercase tracking-wider font-bold">SCHEMES REQUIRED</p>
          <p className="text-sm text-ink-soft leading-relaxed">
            Please add at least one scheme to your portfolio before running a retirement projection.
          </p>
          <Link href="/schemes" className="inline-block border border-ink px-4 py-2 text-xs font-mono font-bold uppercase hover:bg-ink hover:text-paper transition-colors cursor-pointer">
            Select Schemes
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-10">
      <PageHeader
        eyebrow="ARISE Engine · v4"
        title="Retirement odds & paths"
        description="A Student-t Monte Carlo projection across real market volatility — not one guess, a distribution."
        action={<DemoBadge />}
      />

      {!mutation.data && !mutation.isPending && (
        <button
          onClick={() => mutation.mutate()}
          className="border border-ink px-8 py-4 text-sm font-mono uppercase tracking-widest font-semibold hover:bg-ink hover:text-paper transition-colors cursor-pointer"
        >
          Run my retirement projection
        </button>
      )}

      {mutation.isError && (
        <div className="mb-6">
          <QueryError error={mutation.error} />
        </div>
      )}

      {mutation.isPending && (
        <div className="border border-line p-12 flex flex-col items-center justify-center gap-6 min-h-[320px]">
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
            className="w-10 h-10 border-2 border-line border-t-crimson rounded-[1px]"
          />
          <AnimatePresence mode="wait">
            <motion.p
              key={loadingMsg}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              className="text-sm text-ink-soft font-mono"
            >
              {LOADING_MESSAGES[loadingMsg]}
            </motion.p>
          </AnimatePresence>
        </div>
      )}

      {mutation.data && <AriseResults data={mutation.data} />}
    </div>
  );
}

function AriseResults({ data }: { data: AriseResponse }) {
  // Headline the ESTIMATE, not the raw share of surviving paths: a raw
  // 1000/1000 printed as "100%" claims a certainty no simulation can give.
  const estimate = data.success_rate_estimate ?? data.adaptive_success_rate;
  const successRate = estimate * 100;
  const successColorClass = successRate >= 75 ? "text-success" : successRate >= 50 ? "text-ink" : "text-crimson";
  const survived = Math.round(data.adaptive_success_rate * data.n_simulations);

  const chartData = data.median_withdrawal_path.map((p) => ({
    age: p.age,
    withdrawal: p.withdrawal,
  }));

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="space-y-10"
    >
      <div className="grid lg:grid-cols-12 gap-8 items-start">
        <div className="lg:col-span-5 space-y-6 select-none">

          <div className="border border-line p-6 bg-paper space-y-6">
            <div className="space-y-1 text-center border-b border-line pb-6">
              <span className="label">Chance your plan succeeds</span>
              <div className={`font-display text-6xl font-bold tracking-tight tabular-nums ${successColorClass}`}>
                {formatProbability(estimate)}
              </div>
              {data.success_rate_low != null && data.success_rate_high != null && (
                <p className="font-mono text-step--1 text-text-secondary tabular-nums">
                  95% range {formatProbabilityRange(data.success_rate_low, data.success_rate_high)}
                </p>
              )}
              <p className="text-step--1 text-text-secondary pt-1 max-w-sm mx-auto font-sans">
                The money lasts as long as you do, with your lifespan drawn from a mortality curve rather
                than fixed at {data.life_expectancy}. {survived.toLocaleString("en-IN")} of{" "}
                {data.n_simulations.toLocaleString("en-IN")} simulated lifetimes made it.
              </p>
              {(data.parameter_uncertainty_sd ?? 0) > 0 && (
                <p className="text-step--1 text-text-muted max-w-sm mx-auto font-sans">
                  Each lifetime draws its own expected return (±{(data.parameter_uncertainty_sd! * 100).toFixed(1)} pts a
                  year), because {data.n_years_history_used.toFixed(1)} years of history cannot pin it down exactly.
                </p>
              )}
            </div>

            <div className="grid grid-cols-3 gap-4 text-center">
              <div>
                <span className="block text-[9px] font-mono uppercase tracking-wider text-ink-soft">Now</span>
                <span className="font-display text-xl font-bold text-ink tabular-nums">{data.current_age}</span>
              </div>
              <div>
                <span className="block text-[9px] font-mono uppercase tracking-wider text-ink-soft">Retire</span>
                <span className="font-display text-xl font-bold text-ink tabular-nums">{data.retire_age}</span>
              </div>
              <div>
                <span className="block text-[9px] font-mono uppercase tracking-wider text-ink-soft">Plan To</span>
                <span className="font-display text-xl font-bold text-ink tabular-nums">{data.life_expectancy}</span>
              </div>
            </div>
          </div>

          <div className="border border-line p-6 bg-paper space-y-4">
            <div className="flex items-center gap-2 text-crimson">
              <TrendingUp className="w-5 h-5" />
              <span className="font-mono text-xs uppercase tracking-wider font-bold">Recommended Strategy</span>
            </div>
            <p className="font-sans font-bold text-sm text-ink mb-1">{formatLabel(data.primary_strategy)}</p>
            {data.tax_saving_pct > 0 && (
              <p className="text-xs text-success leading-relaxed font-sans">
                Saves an estimated {data.tax_saving_pct.toFixed(1)}% in tax versus a fixed withdrawal schedule.
              </p>
            )}
          </div>

          <div className="border border-line p-6 bg-paper space-y-4">
            <div className="flex justify-between items-center border-b border-line/40 pb-3">
              <span className="font-mono text-[10px] text-ink-soft uppercase tracking-wider font-bold">What we assumed</span>
              <Link href="/profile" className="text-crimson hover:underline text-xs font-mono font-bold">
                Adjust
              </Link>
            </div>

            <div className="space-y-2.5 font-mono text-xs text-ink-soft">
              <div>
                <div className="flex justify-between">
                  <span>Monthly SIP used</span>
                  <span className="font-bold text-ink">{formatINR(data.assumptions.monthly_sip, { compact: false })}</span>
                </div>
                {data.assumptions.monthly_sip_is_estimated && (
                  <span className="block text-[10px] text-crimson mt-1">
                    Estimated — set your real SIP in your profile for a more accurate number.
                  </span>
                )}
              </div>
              <div className="flex justify-between">
                <span>Expected return used</span>
                <span className="font-bold text-ink">{(data.portfolio_cagr_used * 100).toFixed(2)}%</span>
              </div>
              <div className="flex justify-between">
                <span>Raw historical average</span>
                <span className="font-bold text-ink-soft">{(data.shrinkage_diagnostics.portfolio_cagr_raw * 100).toFixed(2)}%</span>
              </div>
            </div>
            <p className="text-[9px] font-mono text-ink-soft/70 leading-normal pt-2 border-t border-line/40">
              The expected return is shrunk toward a long-run prior rather than extrapolating your funds&apos;
              recent history directly.
            </p>
          </div>

        </div>

        <div className="lg:col-span-7 border border-line p-6 bg-paper flex flex-col justify-between min-h-[480px]">
          <div className="select-none mb-6">
            <h3 className="font-display text-lg font-semibold text-ink">Withdrawal Path</h3>
            <p className="text-xs text-ink-soft">
              Median simulated annual withdrawal, in today&apos;s money.
            </p>
          </div>

          <div className="w-full h-[320px] font-mono text-[10px]">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--bg-subtle)" />
                <XAxis dataKey="age" stroke="var(--color-ink-soft)" tickLine={false} />
                <YAxis stroke="var(--color-ink-soft)" tickLine={false} tickFormatter={(v) => formatINR(v)} width={56} />
                <Tooltip
                  formatter={(value) => [formatINR(Number(value), { compact: true }), "Withdrawal"]}
                  labelFormatter={(label) => `Age ${label}`}
                  contentStyle={{
                    backgroundColor: "var(--color-paper)",
                    borderColor: "var(--color-line)",
                    borderRadius: "0px",
                    fontFamily: "var(--font-mono)",
                    fontSize: "11px"
                  }}
                />
                <ReferenceLine x={data.retire_age} stroke="var(--color-crimson)" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="withdrawal" stroke="var(--color-crimson)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Terminal wealth distribution -- REAL percentiles at life_expectancy only,
          not a fabricated per-year interpolated band. */}
      <div className="border border-line p-6 bg-paper select-none">
        <h3 className="font-display text-lg font-semibold text-ink mb-1">Terminal Wealth at Age {data.life_expectancy}</h3>
        <p className="text-xs text-ink-soft mb-8">
          The range of what you could actually end up with, across {data.n_simulations.toLocaleString("en-IN")} simulated markets.
        </p>
        <TerminalWealthSpread data={data} />
      </div>

      {/* Strategy comparison */}
      <div className="border border-line bg-paper overflow-x-auto">
        <table className="w-full text-left text-xs font-mono border-collapse">
          <thead>
            <tr className="text-ink-soft font-semibold uppercase tracking-wider border-b border-line">
              <th className="p-4 font-semibold">Strategy</th>
              <th className="p-4 text-right font-semibold">Success Rate</th>
              <th className="p-4 text-right font-semibold">Income Volatility</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line/40">
            {data.strategy_leaderboard.map((s) => (
              <tr key={s.strategy} className={s.strategy === data.primary_strategy ? "bg-crimson/5" : ""}>
                <td className="p-4 font-sans font-bold text-ink capitalize">
                  {formatLabel(s.strategy)}
                  {s.strategy === data.primary_strategy && (
                    <span className="ml-2 text-[9px] font-mono uppercase text-crimson">recommended</span>
                  )}
                </td>
                <td className="p-4 text-right tabular-nums font-bold text-ink">
                  {formatProbability(s.success_estimate ?? s.success_rate)}
                  {s.success_low != null && s.success_high != null && (
                    <span className="block font-mono text-step--2 font-normal text-text-muted">
                      {formatProbabilityRange(s.success_low, s.success_high)}
                    </span>
                  )}
                </td>
                <td className="p-4 text-right tabular-nums text-ink-soft">{(s.income_volatility * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.milevsky_ruin_prob !== null && (
        <div className="border border-line p-6 bg-paper-tint/30 select-none">
          <h3 className="font-display text-lg font-semibold text-ink mb-2">An Independent Cross-Check</h3>
          <p className="text-xs text-ink-soft leading-relaxed max-w-3xl">
            A closed-form academic model (Milevsky) estimates a{" "}
            <span className="font-mono text-ink font-bold">{(data.milevsky_ruin_prob * 100).toFixed(0)}%</span> ruin probability,
            versus this simulation&apos;s{" "}
            <span className="font-mono text-ink font-bold">
              {data.mc_ruin_prob_constant !== null ? (data.mc_ruin_prob_constant * 100).toFixed(0) : "—"}%
            </span>
            . These are not meant to agree — Milevsky&apos;s model assumes a fixed corpus at retirement with no
            accumulation-phase growth, so it is expected to diverge from a simulation that models years of
            savings ahead of retirement. This is a sanity check, not a second opinion.
          </p>
        </div>
      )}

      {/* Methodology Section */}
      <div className="border border-line p-6 bg-paper select-none space-y-4">
        <div className="flex items-center gap-2 text-ink">
          <HelpCircle className="w-5 h-5 text-ink-soft" />
          <h3 className="font-display text-lg font-semibold">ARISE Simulation Methodology</h3>
        </div>
        <p className="text-xs text-ink-soft leading-relaxed font-sans max-w-4xl">
          We model returns using a Student-t fat-tail distribution rather than a simple log-normal (Gaussian) model.
          Geometric Brownian motion tends to underestimate the frequency of extreme market corrections; a fat-tailed
          distribution accounts for that risk more realistically. The expected return used is shrunk toward a
          long-run prior rather than extrapolated directly from your funds&apos; recent historical average — both
          figures are shown above so you can see the difference for yourself.
        </p>
      </div>

    </motion.div>
  );
}

function TerminalWealthSpread({ data }: { data: AriseResponse }) {
  const points = [
    { key: "p10", label: "10th", value: data.terminal_p10 },
    { key: "p25", label: "25th", value: data.terminal_p25 },
    { key: "p50", label: "Median", value: data.terminal_p50 },
    { key: "p75", label: "75th", value: data.terminal_p75 },
    { key: "p90", label: "90th", value: data.terminal_p90 },
  ];
  const max = data.terminal_p90;

  return (
    <div className="relative h-20">
      <div className="absolute inset-x-0 top-1/2 h-px bg-line -translate-y-1/2" />
      {points.map((p) => {
        const leftPct = max > 0 ? (p.value / max) * 100 : 0;
        const isMedian = p.key === "p50";
        return (
          <motion.div
            key={p.key}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.1 }}
            className="absolute top-0 -translate-x-1/2 flex flex-col items-center"
            style={{ left: `${leftPct}%` }}
          >
            <span className="text-[9px] text-ink-soft mb-1 font-mono uppercase">{p.label}</span>
            <span className={`w-px ${isMedian ? "h-7 bg-crimson" : "h-4 bg-ink-soft/50"}`} />
            <span className={`mt-1 text-xs tabular-nums whitespace-nowrap font-mono ${isMedian ? "text-crimson font-bold" : "text-ink-soft"}`}>
              {formatINR(p.value)}
            </span>
          </motion.div>
        );
      })}
    </div>
  );
}
