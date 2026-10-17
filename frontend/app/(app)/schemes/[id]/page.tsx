"use client";

import React, { use, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getSchemeDetail, getSelectedSchemes, selectSchemes } from "@/lib/api-client";
import { QueryError, LoadingState } from "@/components/query-state";
import { motion } from "motion/react";
import { ArrowLeft, Plus, Minus, AlertTriangle, Users, BookOpen, ShieldCheck } from "lucide-react";

export default function SchemeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const { data: scheme, isLoading, isError, error } = useQuery({
    queryKey: ["scheme-detail", id],
    queryFn: () => getSchemeDetail(id),
  });
  const { data: selected } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });
  // Same fix as the schemes list page, including the more fundamental root
  // cause: POST /schemes/select and GET /schemes/selected return genuinely
  // different shapes on the backend (the POST response has no
  // `selected_scheme_ids` field), so the mutation result can never be
  // trusted to update this query's cache directly -- invalidate and let it
  // actually refetch instead.
  const [optimisticIds, setOptimisticIds] = useState<string[] | null>(null);
  const requestIdRef = useRef(0);
  const latestRequestIdRef = useRef(0);
  const toggleMutation = useMutation({
    mutationFn: async ({ nextIds, requestId }: { nextIds: string[]; requestId: number }) => {
      await selectSchemes(nextIds);
      return { requestId };
    },
    onSuccess: async ({ requestId }) => {
      await queryClient.invalidateQueries({ queryKey: ["selected-schemes"] });
      if (requestId !== latestRequestIdRef.current) return;
      setOptimisticIds(null);
    },
  });

  if (isLoading) return <LoadingState label="Loading scheme factsheet…" />;
  if (isError) return <QueryError error={error} />;

  if (!scheme) {
    return (
      <div className="border border-line border-dashed p-12 text-center select-none bg-paper-tint/10 space-y-4">
        <p className="font-mono text-xs text-crimson uppercase tracking-wider font-bold">SCHEME NOT FOUND</p>
        <Link href="/schemes" className="inline-block border border-ink px-4 py-2 text-xs font-mono font-semibold uppercase hover:bg-ink hover:text-paper transition-colors">
          Return to browser
        </Link>
      </div>
    );
  }

  const heldIds = optimisticIds ?? selected?.selected_scheme_ids ?? [];
  const isHeld = heldIds.includes(scheme.id);
  function toggle() {
    const current = optimisticIds ?? selected?.selected_scheme_ids ?? [];
    const next = current.includes(scheme!.id)
      ? current.filter((sid) => sid !== scheme!.id)
      : [...current, scheme!.id];
    setOptimisticIds(next);
    requestIdRef.current += 1;
    latestRequestIdRef.current = requestIdRef.current;
    toggleMutation.mutate({ nextIds: next, requestId: requestIdRef.current });
  }

  const sipRows = Object.entries(scheme.sip_returns);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="space-y-8"
    >
      <div>
        <Link
          href="/schemes"
          className="inline-flex items-center gap-2 text-xs font-mono font-semibold text-ink-soft hover:text-ink transition-colors uppercase py-1 border border-line bg-paper hover:bg-paper-tint/30 px-3 select-none"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Catalog
        </Link>
      </div>

      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-6 pb-6 border-b border-line">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.16em] text-crimson mb-2 font-medium">
            {scheme.category ?? scheme.instrument_type} • {scheme.subtier ?? scheme.risk_rating} FACTSHEET
          </p>
          <h1 className="font-display text-3xl md:text-4xl font-semibold tracking-tight text-ink">
            {scheme.name}
          </h1>
        </div>

        <button
          onClick={toggle}
          className={`px-5 py-3 text-xs font-mono uppercase tracking-widest font-semibold transition-colors cursor-pointer flex items-center gap-2 w-full md:w-auto justify-center ${
            isHeld ? "bg-crimson text-paper hover:bg-crimson-deep" : "bg-ink text-paper hover:bg-crimson"
          }`}
        >
          {isHeld ? (<><Minus className="w-4 h-4" /> Remove from Portfolio</>) : (<><Plus className="w-4 h-4" /> Include in Portfolio</>)}
        </button>
      </div>

      {/* Stat grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-line border border-line select-none">
        <div className="bg-paper p-5">
          <span className="block text-[10px] font-mono text-ink-soft uppercase tracking-wider mb-1">NAV (Regular Growth)</span>
          <span className="font-display text-2xl font-bold text-ink tabular-nums">
            {scheme.nav_regular_growth ? `₹${scheme.nav_regular_growth.toFixed(2)}` : "—"}
          </span>
        </div>
        <div className="bg-paper p-5">
          <span className="block text-[10px] font-mono text-ink-soft uppercase tracking-wider mb-1">Total AUM Corpus</span>
          <span className="font-display text-2xl font-bold text-ink tabular-nums">
            {scheme.aum_cr ? `₹${scheme.aum_cr.toLocaleString("en-IN")} Cr` : "—"}
          </span>
        </div>
        <div className="bg-paper p-5">
          <span className="block text-[10px] font-mono text-ink-soft uppercase tracking-wider mb-1">Expense Ratio</span>
          <span className="font-display text-2xl font-bold text-ink tabular-nums">
            {scheme.expense_ratio_regular ? `${scheme.expense_ratio_regular.toFixed(2)}%` : "—"}
          </span>
        </div>
        <div className="bg-paper p-5">
          <span className="block text-[10px] font-mono text-ink-soft uppercase tracking-wider mb-1">Min Investment</span>
          <span className="font-display text-2xl font-bold text-ink tabular-nums">
            {scheme.min_investment ? `₹${scheme.min_investment.toLocaleString("en-IN")}` : "—"}
          </span>
        </div>
      </div>

      {/* Real, backend-computed risk disclosure */}
      {scheme.risk_flag && (
        <div className="border border-crimson/30 bg-crimson/5 p-4 flex items-start gap-3 select-none">
          <AlertTriangle className="w-5 h-5 text-crimson shrink-0 mt-0.5" />
          <div className="space-y-1">
            <h4 className="text-xs font-semibold text-crimson uppercase tracking-wider font-mono">Risk Disclosure</h4>
            <p className="text-xs text-ink-soft leading-relaxed max-w-3xl">{scheme.risk_flag}</p>
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-8">

          {/* Real SIP performance vs. benchmark */}
          {sipRows.length > 0 && (
            <div className="border border-line bg-paper p-6">
              <h3 className="font-display text-xl font-semibold text-ink mb-4 select-none">SIP Performance vs. Benchmark</h3>
              <div className="overflow-x-auto select-none">
                <table className="w-full text-left text-xs font-mono border-collapse divide-y divide-line">
                  <thead>
                    <tr className="text-ink-soft font-semibold uppercase tracking-wider">
                      <th className="pb-3 font-semibold">Period</th>
                      <th className="pb-3 text-right font-semibold">Scheme Returns (%)</th>
                      <th className="pb-3 text-right font-semibold">Benchmark (%)</th>
                      <th className="pb-3 text-right font-semibold">Alpha</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/40">
                    {sipRows.map(([period, row]) => {
                      const hasBenchmark = row.benchmark_return_pct !== null;
                      const alpha = hasBenchmark
                        ? parseFloat((row.scheme_return_pct - row.benchmark_return_pct!).toFixed(2))
                        : null;
                      return (
                        <tr key={period} className="hover:bg-paper-tint/10 transition-colors">
                          <td className="py-3 font-semibold text-ink font-sans">{period.replace(/_/g, " ")}</td>
                          <td className="py-3 text-right font-bold text-ink tabular-nums">{row.scheme_return_pct.toFixed(2)}%</td>
                          <td className="py-3 text-right text-ink-soft/80 tabular-nums">
                            {hasBenchmark ? `${row.benchmark_return_pct!.toFixed(2)}%` : "—"}
                          </td>
                          <td className={`py-3 text-right font-bold tabular-nums ${alpha === null ? "text-ink-soft/50" : alpha >= 0 ? "text-success" : "text-crimson"}`}>
                            {alpha === null ? "—" : alpha >= 0 ? `+${alpha}%` : `${alpha}%`}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-6">
            {scheme.sector_allocation.length > 0 && (
              <div className="border border-line bg-paper p-6 space-y-4">
                <h3 className="font-display text-lg font-semibold text-ink select-none">Sector Allocation</h3>
                <div className="space-y-4 select-none">
                  {scheme.sector_allocation.map((sector) => (
                    <div key={sector.sector} className="space-y-1.5">
                      <div className="flex justify-between items-baseline text-xs text-ink-soft">
                        <span className="truncate font-medium font-sans pr-4">{sector.sector}</span>
                        <span className="font-mono font-bold text-ink tabular-nums">
                          {sector.weight !== null ? `${sector.weight.toFixed(1)}%` : "—"}
                        </span>
                      </div>
                      <div className="h-1 w-full bg-line">
                        <div className="h-full bg-crimson" style={{ width: `${Math.min((sector.weight ?? 0) * 3, 100)}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {scheme.top_holdings.length > 0 && (
              <div className="border border-line bg-paper p-6 space-y-4">
                <h3 className="font-display text-lg font-semibold text-ink select-none">Top Holdings</h3>
                <div className="divide-y divide-line/40 font-mono text-xs select-none">
                  {scheme.top_holdings.map((hold, idx) => (
                    <div key={hold.name} className="py-2.5 flex justify-between items-center gap-4 hover:bg-paper-tint/10 px-1 transition-colors">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-ink-soft/50 font-bold w-4">{String(idx + 1).padStart(2, "0")}</span>
                        <span className="font-sans font-medium text-ink truncate pr-3">{hold.name}</span>
                      </div>
                      <span className="font-bold text-ink tabular-nums">{hold.weight.toFixed(2)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

        </div>

        <div className="space-y-6 select-none">
          {scheme.fund_manager && (
            <div className="border border-line bg-paper p-6 space-y-4">
              <div className="flex items-center gap-2.5 text-crimson">
                <Users className="w-5 h-5" />
                <h4 className="font-mono text-xs uppercase tracking-wider font-bold">Fund Manager</h4>
              </div>
              <p className="font-display text-lg font-semibold text-ink">{scheme.fund_manager}</p>
            </div>
          )}

          {scheme.investment_philosophy && (
            <div className="border border-line bg-paper p-6 space-y-4">
              <div className="flex items-center gap-2.5 text-crimson">
                <BookOpen className="w-5 h-5" />
                <h4 className="font-mono text-xs uppercase tracking-wider font-bold">Investment Philosophy</h4>
              </div>
              <p className="text-xs text-ink leading-relaxed font-sans italic">&ldquo;{scheme.investment_philosophy}&rdquo;</p>
            </div>
          )}

          {(scheme.ytm || scheme.modified_duration) && (
            <div className="border border-line bg-paper p-6 space-y-4">
              <div className="flex items-center gap-2.5 text-success">
                <ShieldCheck className="w-5 h-5" />
                <h4 className="font-mono text-xs uppercase tracking-wider font-bold">Debt Metrics</h4>
              </div>
              <div className="space-y-3 font-mono text-xs text-ink-soft">
                {scheme.ytm && (
                  <div className="flex justify-between border-b border-line/40 pb-2">
                    <span>Yield to Maturity (YTM)</span>
                    <span className="font-bold text-ink tabular-nums">{scheme.ytm}</span>
                  </div>
                )}
                {scheme.modified_duration && (
                  <div className="flex justify-between border-b border-line/40 pb-2">
                    <span>Modified Duration</span>
                    <span className="font-bold text-ink tabular-nums">{scheme.modified_duration}</span>
                  </div>
                )}
                {scheme.avg_maturity && (
                  <div className="flex justify-between">
                    <span>Average Maturity</span>
                    <span className="font-bold text-ink tabular-nums">{scheme.avg_maturity}</span>
                  </div>
                )}
              </div>
              <p className="text-[10px] text-ink-soft leading-normal font-sans pt-1">
                Duration measures sensitivity to interest-rate changes — a higher number means the NAV moves more when rates shift.
              </p>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
