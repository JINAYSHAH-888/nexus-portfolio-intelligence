"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getPlos, getSelectedSchemes } from "@/lib/api-client";
import { PageHeader, DemoBadge, StatCard } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { motion, AnimatePresence } from "motion/react";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
} from "recharts";
import { AlertTriangle, ChevronDown, Award, HelpCircle } from "lucide-react";
import { schemeShortName } from "@/lib/utils";
import type { PairPlos } from "@/lib/api-types";

function toneFor(score: number) {
  if (score >= 70) return { color: "var(--err)", label: "Very High overlap — minimal diversification benefit" };
  if (score >= 50) return { color: "var(--accent)", label: "High overlap — significant shared exposure" };
  if (score >= 30) return { color: "var(--warn)", label: "Moderate overlap — some shared risk" };
  return { color: "var(--ok)", label: "Low overlap — good diversification" };
}

export default function PlosPage() {
  const { data: selected, isLoading: selLoading } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });
  const schemeCount = selected?.selected_scheme_ids?.length ?? 0;

  const { data: plos, isLoading, isError, error } = useQuery({
    queryKey: ["plos"],
    queryFn: getPlos,
    enabled: schemeCount >= 2,
  });

  const [expandedPair, setExpandedPair] = useState<string | null>(null);

  const radarPair = useMemo(() => {
    if (!plos || plos.pair_scores.length === 0) return null;
    return plos.pair_scores.slice().sort((a, b) => b.plos_score - a.plos_score)[0];
  }, [plos]);

  const radarData = useMemo(() => {
    if (!radarPair) return [];
    return radarPair.layers.map((l) => ({
      layer: l.description.length > 22 ? l.description.slice(0, 20) + "…" : l.description,
      score: Math.round(l.raw_score * 100),
    }));
  }, [radarPair]);

  if (selLoading) return <LoadingState label="Loading your selection…" />;

  if (schemeCount < 2) {
    return (
      <div className="space-y-8">
        <PageHeader
          eyebrow="PLOS Engine · Diagnostics"
          title="Portfolio Layered Overlap Score"
          description="A 10-layer weighted diversification score across every pair of schemes you select."
        />
        <div className="border border-line border-dashed p-12 text-center select-none bg-paper-tint/10 max-w-xl mx-auto space-y-4">
          <AlertTriangle className="w-8 h-8 text-crimson mx-auto" />
          <p className="font-mono text-xs text-crimson uppercase tracking-wider font-bold">MULTIPLE POSITIONS REQUIRED</p>
          <p className="text-sm text-ink-soft leading-relaxed">
            PLOS requires at least two selected schemes to score. Currently, you have {schemeCount}.
          </p>
          <Link href="/schemes" className="inline-block border border-ink px-4 py-2 text-xs font-mono font-bold uppercase hover:bg-ink hover:text-paper transition-colors cursor-pointer">
            Select Schemes
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading) return <LoadingState label="Loading PLOS analysis…" />;
  if (isError) return <QueryError error={error} />;
  if (!plos) return null;

  const tone = toneFor(plos.portfolio_plos);
  const rankedPairs = plos.pair_scores.slice().sort((a, b) => b.plos_score - a.plos_score);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="space-y-10"
    >
      <PageHeader
        eyebrow="PLOS Engine · 10 weighted layers"
        title="Your diversification score"
        description={plos.interpretation}
        action={<DemoBadge />}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 select-none">
        <StatCard
          label="Portfolio PLOS"
          value={<span style={{ color: tone.color }}>{plos.portfolio_plos.toFixed(1)}</span>}
        />
        <StatCard
          label="Diversified"
          value={<span className="text-success">{plos.diversification_score.toFixed(1)}%</span>}
        />
        <StatCard label="Scheme Pairs Scored" value={plos.pair_scores.length} />
        <StatCard label="Data Source" value={<span className="text-xs font-mono">{plos.data_source}</span>} />
      </div>

      {/* Top 5 recommendation -- real greedy-minimization algorithm, no fabricated confidence scores */}
      {plos.top_5_recommendation.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <Award className="w-5 h-5 text-crimson" />
            <h2 className="font-display text-xl font-semibold text-ink">Top 5 — schemes that minimize overlap together</h2>
          </div>
          <p className="text-xs text-ink-soft mb-6 max-w-2xl">
            Computed by greedily selecting the 5 schemes with the lowest average pairwise PLOS score among each other —
            i.e. the combination that overlaps the least, not a subjective &ldquo;buy&rdquo; call.
          </p>
          <div className="grid md:grid-cols-2 lg:grid-cols-5 gap-4">
            {plos.top_5_recommendation.map((rec) => (
              <div key={rec.scheme_id} className="border border-line bg-paper p-4 space-y-2">
                <span className="font-mono text-[10px] text-crimson font-bold uppercase">#{rec.rank}</span>
                <h3 className="font-display text-sm font-semibold text-ink leading-snug">{schemeShortName(rec.scheme_name)}</h3>
                <div className="text-[10px] font-mono text-ink-soft">
                  Avg. overlap w/ others: <span className="font-bold text-ink">{rec.avg_plos_with_others.toFixed(1)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Radar: real layer scores for the most overlapping pair */}
      {radarPair && (
        <div className="border border-line bg-paper p-6">
          <h3 className="font-display text-lg font-semibold text-ink mb-1">
            Layer Breakdown — {schemeShortName(radarPair.scheme_a_name)} × {schemeShortName(radarPair.scheme_b_name)}
          </h3>
          <p className="text-xs text-ink-soft mb-6">Your most overlapping pair, across all 10 real weighted layers.</p>
          <div className="w-full h-[360px]">
            <ResponsiveContainer width="100%" height="100%">
              <RadarChart data={radarData}>
                <PolarGrid stroke="var(--color-line)" />
                <PolarAngleAxis dataKey="layer" tick={{ fontSize: 9, fill: "var(--color-ink-soft)" }} />
                <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 8 }} />
                <Radar dataKey="score" stroke="var(--color-crimson)" fill="var(--color-crimson)" fillOpacity={0.25} />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* All pairs ranked, each expandable to see the real per-layer contribution */}
      <div>
        <h2 className="font-display text-xl font-semibold text-ink mb-4">All Pairs — Ranked</h2>
        <div className="space-y-3">
          {rankedPairs.map((pair) => (
            <PairCard
              key={`${pair.scheme_a_id}-${pair.scheme_b_id}`}
              pair={pair}
              isOpen={expandedPair === `${pair.scheme_a_id}-${pair.scheme_b_id}`}
              onToggle={() =>
                setExpandedPair((cur) =>
                  cur === `${pair.scheme_a_id}-${pair.scheme_b_id}` ? null : `${pair.scheme_a_id}-${pair.scheme_b_id}`
                )
              }
            />
          ))}
        </div>
      </div>

      <div className="border border-line p-6 bg-paper select-none space-y-4">
        <div className="flex items-center gap-2 text-ink">
          <HelpCircle className="w-5 h-5 text-ink-soft" />
          <h3 className="font-display text-lg font-semibold">PLOS Methodology</h3>
        </div>
        <p className="text-xs text-ink-soft leading-relaxed font-sans max-w-4xl">
          The Portfolio Layered Overlap Score blends 10 weighted layers per pair of schemes: two real-market
          correlation layers (return correlation and sector return correlation, weighted 30% and 10%) and eight
          holdings/fundamental layers (direct stock overlap, top-10 overlap, sector similarity, weighted stock
          overlap, category/benchmark match, risk profile proximity, fundamental quality, and valuation bucket —
          each independently weighted, summing to 100%). Every layer&apos;s exact weight and contribution is shown
          in the breakdown above, not collapsed into an unexplainable single number.
        </p>
      </div>

    </motion.div>
  );
}

function PairCard({ pair, isOpen, onToggle }: { pair: PairPlos; isOpen: boolean; onToggle: () => void }) {
  const tone = toneFor(pair.plos_score);
  return (
    <div className="border border-line bg-paper">
      <button onClick={onToggle} className="w-full flex items-center justify-between gap-4 p-5 text-left cursor-pointer">
        <div>
          <span className="text-sm font-sans font-bold text-ink">
            {schemeShortName(pair.scheme_a_name)} × {schemeShortName(pair.scheme_b_name)}
          </span>
          <span className="block text-xs text-ink-soft mt-1">{pair.interpretation}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="font-display text-2xl font-bold tabular-nums" style={{ color: tone.color }}>
            {pair.plos_score.toFixed(1)}
          </span>
          <motion.span animate={{ rotate: isOpen ? 180 : 0 }} transition={{ duration: 0.2 }}>
            <ChevronDown size={18} className="text-ink-soft" />
          </motion.span>
        </div>
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden"
          >
            <div className="p-5 pt-0 space-y-2.5">
              {pair.real_correlation !== null && (
                <p className="text-xs text-ink-soft mb-3 font-mono">
                  Real return correlation: <span className="text-ink font-bold">{pair.real_correlation.toFixed(2)}</span>
                  {" · "}data quality: {pair.data_quality}
                </p>
              )}
              {pair.layers.map((layer) => (
                <div key={layer.layer_id} className="flex items-center gap-3">
                  <span className="w-56 shrink-0 text-xs text-ink-soft truncate" title={layer.layer_id}>
                    {layer.description}
                  </span>
                  <div className="flex-1 h-2 bg-line relative">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${layer.raw_score * 100}%` }}
                      transition={{ duration: 0.5 }}
                      className="absolute inset-y-0 left-0 bg-crimson"
                    />
                  </div>
                  <span className="w-16 text-right text-xs tabular-nums text-ink-soft font-mono">w={layer.weight}</span>
                  <span className="w-14 text-right text-xs tabular-nums font-bold text-ink font-mono">
                    {(layer.contribution * 100).toFixed(1)}
                  </span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
