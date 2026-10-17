"use client";

import React, { useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getRisk, getSelectedSchemes } from "@/lib/api-client";
import { PageHeader, StatCard, DemoBadge } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { motion } from "motion/react";
import { AlertTriangle } from "lucide-react";
import { schemeShortName } from "@/lib/utils";
import { ConditionalRiskPanel } from "@/components/app/conditional-risk";

export default function RiskAnalysisPage() {
  const { data: selected, isLoading: selLoading } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });
  const schemeCount = selected?.selected_scheme_ids?.length ?? 0;

  const { data: risk, isLoading, isError, error } = useQuery({
    queryKey: ["risk"],
    queryFn: getRisk,
    enabled: schemeCount >= 1,
  });

  const instrumentTypeById = useMemo(() => {
    const m = new Map<string, string>();
    (selected?.schemes ?? []).forEach((s) => m.set(s.id, s.instrument_type));
    return m;
  }, [selected]);

  if (selLoading) return <LoadingState label="Loading your selection…" />;

  if (schemeCount < 1) {
    return (
      <div className="space-y-8">
        <PageHeader
          eyebrow="Risk Engine"
          title="Portfolio Risk Parameters"
          description="Quantifies standard deviations, Sharpe metrics, and Value-at-Risk limits."
        />
        <div className="border border-line border-dashed p-12 text-center select-none bg-paper-tint/10 max-w-xl mx-auto space-y-4">
          <AlertTriangle className="w-8 h-8 text-crimson mx-auto" />
          <p className="font-mono text-xs text-crimson uppercase tracking-wider font-bold">SCHEMES REQUIRED</p>
          <p className="text-sm text-ink-soft leading-relaxed">
            Please add at least one scheme to your portfolio to compute risk parameters.
          </p>
          <Link href="/schemes" className="inline-block border border-ink px-4 py-2 text-xs font-mono font-bold uppercase hover:bg-ink hover:text-paper transition-colors cursor-pointer">
            Select Schemes
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading) return <LoadingState label="Loading risk analysis…" />;
  if (isError) return <QueryError error={error} />;
  if (!risk) return null;

  const maxDrawdownAbs = Math.abs(risk.portfolio_max_drawdown * 100);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="space-y-10"
    >
      <PageHeader
        eyebrow="Risk Engine"
        title="Risk metrics & correlation"
        description="Ledoit-Wolf shrinkage covariance, parametric and historical VaR/CVaR, real portfolio-level diversification."
        action={<DemoBadge />}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 select-none">
        {/* Tone, unit and provenance now come from StatCard props rather than
            nested markup, so every figure across the app is typeset the same
            way: mono label above, mono tabular value, unit as a quiet aside. */}
        <StatCard
          label="Portfolio annualised return"
          tone={risk.portfolio_annualized_return >= 0 ? "ok" : "err"}
          value={`${(risk.portfolio_annualized_return * 100).toFixed(1)}%`}
          aside={<span className="label">CAGR</span>}
          note={
            risk.weighting_scheme === "investor"
              ? "At your actual allocation weights."
              : "Equal-weighted — no allocation saved yet."
          }
        />
        <StatCard
          variant="ink"
          label="Portfolio volatility"
          value={`${(risk.portfolio_volatility * 100).toFixed(1)}%`}
          aside={<span className="label">Std dev</span>}
        />
        <StatCard
          label="Sharpe ratio"
          value={risk.portfolio_sharpe.toFixed(2)}
          aside={<span className="label">Per unit risk</span>}
          note="Excess return over the risk-free rate, per unit of volatility."
        />
        <StatCard
          label="Portfolio max drawdown"
          tone="err"
          value={`-${maxDrawdownAbs.toFixed(1)}%`}
          aside={<span className="label">Peak to trough</span>}
          note="The worst fall you would have had to sit through."
        />
      </div>

      {risk.conditional_risk && risk.conditional_risk.var_95_1d != null && (
        <ConditionalRiskPanel cr={risk.conditional_risk} />
      )}

      {/* Diversification Ratio */}
      <div className="border border-line p-6 bg-paper select-none space-y-6">
        <div>
          <h3 className="font-display text-lg font-semibold text-ink mb-1">Diversification Ratio</h3>
          <p className="text-xs text-ink-soft">
            A ratio below 1.0 means your blend is genuinely reducing risk, not just averaging it. Yours is{" "}
            <span className="text-crimson font-semibold font-mono">{risk.diversification_ratio.toFixed(3)}</span>.
          </p>
        </div>

        <div className="space-y-4">
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-ink-soft font-mono font-semibold uppercase">
              <span>If your funds moved independently</span>
              <span className="text-ink">{(risk.weighted_avg_vol * 100).toFixed(2)}%</span>
            </div>
            <div className="h-2 w-full bg-line">
              <div className="h-full bg-ink-soft/40 w-full" />
            </div>
          </div>

          <div className="space-y-1">
            <div className="flex justify-between text-xs text-ink-soft font-mono font-semibold uppercase">
              <span>Your actual portfolio volatility</span>
              <span className="text-crimson font-bold">{(risk.portfolio_volatility * 100).toFixed(2)}%</span>
            </div>
            <div className="h-2 w-full bg-line">
              <div
                className="h-full bg-crimson"
                style={{ width: `${Math.min(risk.diversification_ratio, 1) * 100}%` }}
              />
            </div>
          </div>
        </div>

        <div className="bg-paper-tint/30 p-3 text-[10px] font-mono text-ink-soft border-t border-line/50">
          {risk.diversification_ratio < 1
            ? "Your allocation is genuinely reducing volatility through diversification."
            : "Your portfolio is highly correlated, providing little natural hedge benefit."}
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 border border-line p-6 bg-paper select-none space-y-6 overflow-x-auto">
          <div>
            <h3 className="font-display text-lg font-semibold text-ink mb-1">Correlation Matrix</h3>
            <p className="text-xs text-ink-soft">
              Diverging scale: <span className="text-success font-semibold">negative (-1.0)</span> to{" "}
              <span className="text-crimson font-semibold">positive (+1.0)</span>.
            </p>
          </div>

          <div className="min-w-[440px] max-w-xl mx-auto py-4">
            <div className="grid" style={{ gridTemplateColumns: `100px repeat(${risk.correlation_labels.length}, minmax(0, 1fr))` }}>
              <div className="h-8" />
              {risk.correlation_labels.map((name, i) => (
                <div key={i} className="h-8 flex items-center justify-center font-mono text-[9px] font-bold text-ink-soft uppercase text-center truncate px-1">
                  {schemeShortName(name).slice(0, 4).toUpperCase()}
                </div>
              ))}
            </div>

            {risk.correlation_labels.map((rowName, i) => (
              <div key={i} className="grid" style={{ gridTemplateColumns: `100px repeat(${risk.correlation_labels.length}, minmax(0, 1fr))` }}>
                <div className="h-10 flex items-center justify-end font-mono text-[9px] font-bold text-ink-soft uppercase truncate pr-2">
                  {schemeShortName(rowName).slice(0, 4).toUpperCase()}
                </div>
                {risk.correlation_labels.map((colName, j) => {
                  const val = risk.correlation_matrix[i][j];
                  let bg = "var(--color-paper)";
                  let color = "var(--color-ink)";
                  if (val > 0) {
                    bg = `color-mix(in oklch, var(--accent) ${Math.round(val * 100)}%, transparent)`;
                    if (val > 0.45) color = "var(--text)";
                  } else if (val < 0) {
                    bg = `color-mix(in oklch, var(--ok) ${Math.round(Math.abs(val) * 100)}%, transparent)`;
                    if (Math.abs(val) > 0.45) color = "var(--text)";
                  }
                  return (
                    <div
                      key={j}
                      style={{ backgroundColor: bg, color }}
                      className="h-10 relative flex cursor-help items-center justify-center border border-line font-mono text-xs font-bold tabular-nums outline-offset-[-2px] transition-[outline-color] duration-150 outline-2 outline-transparent hover:z-10 hover:outline-accent focus-visible:z-10 focus-visible:outline-accent"
                      title={`${rowName} × ${colName}: ${val.toFixed(2)}`}
                    >
                      {val.toFixed(2)}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        <div className="border border-line p-6 bg-paper select-none space-y-4">
          <h3 className="font-display text-lg font-semibold text-ink">Per-Scheme Risk</h3>
          <p className="text-xs text-ink-soft leading-relaxed">
            Individual statistical breakdowns from real NAV history.
          </p>

          <div className="divide-y divide-line/40 space-y-4 overflow-y-auto max-h-[440px] pr-1">
            {risk.scheme_metrics.map((fund) => (
              <div key={fund.scheme_id} className="pt-4 first:pt-0 space-y-2">
                <div className="flex justify-between items-start gap-3">
                  <div>
                    <h4 className="text-xs font-sans font-bold text-ink leading-tight">{schemeShortName(fund.scheme_name)}</h4>
                    {instrumentTypeById.get(fund.scheme_id) && (
                      <span className="font-mono text-[9px] text-ink-soft uppercase">({instrumentTypeById.get(fund.scheme_id)})</span>
                    )}
                  </div>
                  {fund.is_synthetic && (
                    <span className="font-mono text-[8px] bg-crimson/10 text-crimson border border-crimson/25 px-1.5 py-0.5 uppercase font-bold shrink-0">
                      modeled
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-3 gap-2 font-mono text-[10px] text-ink-soft">
                  <div>
                    <span className="block text-[8px] text-ink-soft/60 uppercase">Return</span>
                    <span className="font-bold text-ink tabular-nums">{(fund.annualized_return * 100).toFixed(1)}%</span>
                  </div>
                  <div>
                    <span className="block text-[8px] text-ink-soft/60 uppercase">Volatility</span>
                    <span className="font-bold text-ink tabular-nums">{(fund.annualized_volatility * 100).toFixed(1)}%</span>
                  </div>
                  <div>
                    <span className="block text-[8px] text-ink-soft/60 uppercase">Sharpe</span>
                    <span className="font-bold text-ink tabular-nums">{fund.sharpe_ratio.toFixed(2)}</span>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2 font-mono text-[10px] text-ink-soft bg-paper-tint/20 p-2 border border-line/40">
                  <div>
                    <span className="block text-[8px] text-ink-soft/60 uppercase">VaR 95% (Daily)</span>
                    <span className="font-bold text-crimson tabular-nums">{(fund.var_95_parametric * 100).toFixed(2)}%</span>
                  </div>
                  <div>
                    <span className="block text-[8px] text-ink-soft/60 uppercase">CVaR 95%</span>
                    <span className="font-bold text-crimson-deep tabular-nums">{(fund.cvar_95_parametric * 100).toFixed(2)}%</span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <p className="text-[9px] font-mono text-ink-soft/80 leading-normal pt-2 border-t border-line/50">
            * VaR/CVaR 95%: in the worst 5% of trading days, expect a loss of at least the VaR value. CVaR is the average loss within that worst-5% bucket.
          </p>
        </div>

      </div>

    </motion.div>
  );
}
