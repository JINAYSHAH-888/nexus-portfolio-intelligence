"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { getPlos, getSelectedSchemes } from "@/lib/api-client";
import { PageHeader, DemoBadge, StatCard, DataPanel } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { schemeShortName } from "@/lib/utils";
import type { PairPlos, PlosResponse } from "@/lib/api-types";

/* PLOS v2 = 100 x the pair's predicted future co-movement (backend/ml). */
function band(score: number): { label: string; cls: string } {
  if (score >= 70) return { label: "Behave as one fund", cls: "text-accent-text" };
  if (score >= 50) return { label: "Heavy overlap", cls: "text-accent-text" };
  if (score >= 30) return { label: "Some shared risk", cls: "text-warn" };
  return { label: "Genuinely different", cls: "text-ok" };
}

const MODEL_NAMES: Record<string, string> = {
  v1_hand_weights: "PLOS v1 · hand-set weights",
  v1_as_shipped: "PLOS v1 · as originally shipped",
  hand_weights_fisher_z: "v1 weights, scored in Fisher-z",
  v2_learned_linear: "Learned linear weights",
  v2_monotone_additive: "PLOS v2 · monotone additive",
  v3_learned_shrunk: "PLOS v3 · learned, shrunk (used)",
  monotone_trees: "Gradient-boosted trees",
  gradient_boosting_depth2: "Gradient boosting, depth 2 (benchmark)",
};
const USED = new Set(["v2_monotone_additive", "v3_learned_shrunk"]);

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
  const [open, setOpen] = useState<string | null>(null);

  if (selLoading) return <LoadingState label="Loading your selection…" />;

  if (schemeCount < 2) {
    return (
      <div className="space-y-8">
        <PageHeader
          eyebrow="PLOS engine"
          title="Your diversification score"
          description="How much your funds really move together — learned from thirteen years of NAV history."
        />
        <div className="ticked mx-auto max-w-xl space-y-4 border border-dashed border-line bg-bg p-12 text-center">
          <p className="label text-accent-text">Two or more schemes required</p>
          <p className="text-step-0 leading-relaxed text-text-secondary">
            PLOS scores pairs of schemes. You currently hold {schemeCount}.
          </p>
          <Link href="/schemes" className="btn btn-primary">
            Select schemes
          </Link>
        </div>
      </div>
    );
  }

  if (isLoading) return <LoadingState label="Scoring every pair…" />;
  if (isError) return <QueryError error={error} />;
  if (!plos) return null;

  // v2 and v3 are both learned; v3 adds the quant layers and Shapley values.
  const v2 = plos.model_version === "v2" || plos.model_version === "v3";
  const names = Object.fromEntries(
    plos.pair_scores.flatMap((p) => [
      [p.scheme_a_id, p.scheme_a_name],
      [p.scheme_b_id, p.scheme_b_name],
    ]),
  );
  const ranked = plos.pair_scores.slice().sort((a, b) => b.plos_score - a.plos_score);

  return (
    <div className="space-y-10">
      <PageHeader
        eyebrow={v2 ? `PLOS engine · ${plos.model_version} · machine-learned` : "PLOS engine"}
        title="Your diversification score"
        description={
          v2
            ? "Each pair is scored by how strongly its two funds are predicted to move together — a model trained on real AMFI NAV history, with every layer's contribution shown."
            : plos.interpretation
        }
        action={<DemoBadge />}
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {v2 && plos.effective_number_of_funds != null ? (
          <StatCard
            variant="ink"
            label="You really hold"
            value={`${plos.effective_number_of_funds.toFixed(1)} funds`}
            note={`of ${plos.scheme_ids.length} selected, by how they move`}
          />
        ) : (
          <StatCard variant="ink" label="Portfolio PLOS" value={plos.portfolio_plos.toFixed(1)} />
        )}
        <StatCard label="Portfolio PLOS" value={plos.portfolio_plos.toFixed(1)} note={band(plos.portfolio_plos).label} />
        <StatCard label="Diversified" tone="ok" value={`${plos.diversification_score.toFixed(1)}%`} />
        <StatCard
          label="Pairs scored"
          value={plos.pair_scores.length}
          note={plos.data_source === "amfi_nav_history" ? "AMFI NAV history + holdings" : plos.data_source}
        />
      </div>

      {v2 && plos.redundancy_clusters && <Clusters plos={plos} names={names} />}

      <section className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-display text-step-2 font-semibold text-text">Every pair, ranked</h2>
          <span className="font-mono text-step--2 text-text-muted">open a pair to see each layer&apos;s contribution</span>
        </div>
        <div className="space-y-3">
          {ranked.map((pair) => {
            const id = `${pair.scheme_a_id}-${pair.scheme_b_id}`;
            return <PairCard key={id} pair={pair} v2={v2} isOpen={open === id} onToggle={() => setOpen(open === id ? null : id)} />;
          })}
        </div>
      </section>

      {plos.top_5_recommendation.length > 0 && (
        <DataPanel label="Least-overlapping combination" note="greedy minimum of average pairwise PLOS">
          <ol className="grid divide-line sm:grid-cols-2 sm:divide-x lg:grid-cols-5">
            {plos.top_5_recommendation.map((rec) => (
              <li key={rec.scheme_id} className="space-y-1 border-b border-line p-5 sm:border-b-0">
                <span className="label text-accent-text">#{rec.rank}</span>
                <p className="font-medium leading-snug text-text">{schemeShortName(rec.scheme_name)}</p>
                <p className="font-mono text-step--2 text-text-muted">
                  avg PLOS with the rest <span data-numeric className="text-text">{rec.avg_plos_with_others.toFixed(1)}</span>
                </p>
              </li>
            ))}
          </ol>
        </DataPanel>
      )}

      {v2 && plos.model_validation && <Validation plos={plos} />}
    </div>
  );
}

function Clusters({ plos, names }: { plos: PlosResponse; names: Record<string, string> }) {
  const clusters = plos.redundancy_clusters ?? [];
  const merged = clusters.filter((c) => c.length > 1);
  return (
    <section className="ticked grid gap-8 border border-line bg-bg p-6 lg:grid-cols-12">
      <div className="space-y-3 lg:col-span-4">
        <span className="ink-rule" aria-hidden="true" />
        <p className="label text-accent-text">Redundancy map</p>
        <h2 className="font-display text-step-3 font-semibold leading-tight text-text">
          {merged.length === 0
            ? "No two of your funds behave as one."
            : `${merged.reduce((n, c) => n + c.length, 0)} of your funds move as ${merged.length === 1 ? "one block" : `${merged.length} blocks`}.`}
        </h2>
        <p className="text-step--1 leading-relaxed text-text-secondary">
          Funds are grouped when their predicted co-movement is above 60. Inside a group, adding money to a second
          fund buys almost no diversification.
        </p>
      </div>
      <ul className="grid content-start gap-3 sm:grid-cols-2 lg:col-span-8">
        {clusters.map((c, i) => (
          <li key={i} className={`p-4 ${c.length > 1 ? "ink-panel" : "border border-line bg-bg-subtle"}`}>
            <p className="label mb-2">{c.length > 1 ? `Behaves as one · ${c.length} funds` : "Independent"}</p>
            <ul className="space-y-1">
              {c.map((id) => (
                <li key={id} className="text-step--1 font-medium text-text">
                  {schemeShortName(names[id] ?? id)}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

function PairCard({ pair, v2, isOpen, onToggle }: { pair: PairPlos; v2: boolean; isOpen: boolean; onToggle: () => void }) {
  const b = band(pair.plos_score);
  const layers = pair.layers.slice().sort((x, y) => y.contribution - x.contribution);
  const maxC = Math.max(...layers.map((l) => l.contribution), 0.0001);
  return (
    <div className="border border-line bg-bg">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full cursor-pointer items-center justify-between gap-4 p-5 text-left transition-colors hover:bg-bg-hover"
      >
        <div className="min-w-0">
          <span className="font-medium text-text">
            {schemeShortName(pair.scheme_a_name)} × {schemeShortName(pair.scheme_b_name)}
          </span>
          <span className={`mt-1 block font-mono text-step--2 ${b.cls}`}>{b.label}</span>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <div className="hidden h-1.5 w-32 bg-bg-subtle sm:block" aria-hidden="true">
            <div className="h-full bg-[var(--mark)]" style={{ width: `${pair.plos_score}%` }} />
          </div>
          <span data-numeric className="w-14 text-right text-step-2 font-medium text-text">
            {pair.plos_score.toFixed(1)}
          </span>
          <ChevronDown size={18} className={`text-text-muted transition-transform ${isOpen ? "rotate-180" : ""}`} />
        </div>
      </button>

      {isOpen && (
        <div className="space-y-3 border-t border-line p-5">
          <p className="font-mono text-step--2 text-text-muted">
            {pair.real_correlation !== null ? (
              <>
                Weekly return correlation, last two years:{" "}
                <span data-numeric className="text-text">{pair.real_correlation.toFixed(2)}</span>
              </>
            ) : (
              "No verified NAV history for one of these funds — scored on holdings alone."
            )}
          </p>
          {layers.map((l) => (
            <div key={l.layer_id} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 sm:grid-cols-[minmax(0,22rem)_1fr_auto]">
              <span className="text-step--1 text-text-secondary">{l.description}</span>
              <div className="relative order-3 col-span-2 h-1.5 bg-bg-subtle sm:order-none sm:col-span-1" aria-hidden="true">
                <div className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${(l.contribution / maxC) * 100}%` }} />
              </div>
              <span data-numeric className="text-right font-mono text-step--2 text-text">
                {v2 ? `+${(l.contribution * 100).toFixed(1)}` : `${(l.contribution * 100).toFixed(1)}`}
                <span className="ml-2 text-text-muted">({(l.raw_score * 100).toFixed(0)}%)</span>
              </span>
            </div>
          ))}
          {v2 && (
            <p className="pt-1 font-mono text-step--2 text-text-muted">
              Points each layer adds to the score (layer&apos;s own reading in brackets). The rest is the baseline any two
              funds share.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Validation({ plos }: { plos: PlosResponse }) {
  const v = plos.model_validation!;
  const v3 = plos.model_version === "v3";
  const rows = Object.entries(v.r2).sort((a, b) => b[1] - a[1]);
  return (
    <DataPanel label={`How PLOS ${plos.model_version} was trained and tested`} note={`${v.n_pairs.toLocaleString("en-IN")} real fund pairs`}>
      <div className="grid lg:grid-cols-2">
        <table className="w-full border-collapse text-step--1">
          <thead>
            <tr className="border-b border-line">
              <th className="p-4 text-left font-normal"><span className="label">Model</span></th>
              <th className="p-4 text-right font-normal"><span className="label">{v3 ? "Forward in time R²" : "Accuracy (R²)"}</span></th>
              {v3 && v.r2_loso && <th className="p-4 text-right font-normal"><span className="label">Fund held out R²</span></th>}
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, r2]) => (
              <tr key={k} className={`border-b border-line last:border-0 ${USED.has(k) ? "bg-bg-subtle" : ""}`}>
                <td className="p-4 text-text">{MODEL_NAMES[k] ?? k}</td>
                <td data-numeric className="p-4 text-right text-text">{r2.toFixed(3)}</td>
                {v3 && v.r2_loso && (
                  <td data-numeric className="p-4 text-right text-text-secondary">{v.r2_loso[k]?.toFixed(3) ?? "—"}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="space-y-3 border-t border-line p-5 text-step--1 leading-relaxed text-text-secondary lg:border-l lg:border-t-0">
          <p>
            <span className="font-medium text-text">Target:</span> how strongly two funds actually moved together
            afterwards. Every layer was measured strictly before that window.
            {v3 && " The model is chosen on the forward-in-time test: trained on earlier years only, then asked to predict each of the last four."}
          </p>
          {v.findings?.map((f) => (
            <p key={f} className="border-l-2 border-accent pl-3">{f}</p>
          ))}
          <p className="font-mono text-step--2 text-text-muted">
            Return layers {v.returns_window[0]} to {v.returns_window[1]} · AMFI codes verified against each
            factsheet&apos;s NAV · stock prices for {">"}90% of each Indian fund&apos;s holdings
          </p>
        </div>
      </div>
    </DataPanel>
  );
}
