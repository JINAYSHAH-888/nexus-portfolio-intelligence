"use client";

import React, { useState, useMemo, useRef } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getFilteredSchemes, getSelectedSchemes, selectSchemes } from "@/lib/api-client";
import { PageHeader, DemoBadge } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { motion, AnimatePresence } from "motion/react";
import { Plus, Minus, Search, Check, AlertTriangle } from "lucide-react";

const INSTRUMENT_TYPES = ["All", "Equity", "Debt"] as const;
const SUBTIERS = ["All", "Core", "Growth", "Aggressive"] as const;

export default function SchemesPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["schemes-filtered"],
    queryFn: getFilteredSchemes,
  });
  const { data: selected } = useQuery({
    queryKey: ["selected-schemes"],
    queryFn: getSelectedSchemes,
    retry: 0,
  });

  // THE FIX for a real, confirmed bug: rapid clicks (selecting several
  // schemes in quick succession) each fired a mutation whose payload was
  // computed from `selected?.selected_scheme_ids` -- the QUERY CACHE's
  // value at click time. Since `selectSchemes()` REPLACES the whole
  // selection server-side, and the cache only updates once a mutation's
  // `onSuccess` fires, a second click landing before the first one resolves
  // read the SAME stale (pre-first-click) list, computed a next state that
  // dropped the first click entirely, and overwrote it -- so only the last
  // click to actually resolve ever stuck, regardless of click order.
  //
  // `optimisticIds` is the single synchronous source of truth for what
  // toggle() reads: updated immediately, in the same tick as the click, so
  // every subsequent click -- even ones fired before any network request
  // has resolved -- correctly accumulates on top of the previous one.
  // `latestRequestId` additionally discards any mutation response that
  // isn't the most recently fired one, so an older, slower request that
  // happens to resolve AFTER a newer one can never clobber state (a real,
  // subtler version of the same race).
  //
  // SECOND, MORE FUNDAMENTAL BUG this fixes: the mutation's response
  // (POST /schemes/select) and the query it needs to keep in sync
  // (GET /schemes/selected) are genuinely DIFFERENT shapes on the backend
  // -- the POST response has no `selected_scheme_ids` field at all.
  // Caching that mismatched response directly into the query slot (an
  // earlier version of this code did exactly that) silently wiped
  // `selected_scheme_ids` to `undefined` on every successful click, so
  // EVERY click after the first computed its next list from an empty base
  // -- which is the real reason only one scheme ever stayed selected.
  // The fix is to never trust the mutation response's shape for this at
  // all: invalidate and let TanStack Query actually refetch the real
  // GET /schemes/selected, and keep `optimisticIds` authoritative in the
  // UI until that refetch genuinely completes (awaited here, not fired
  // and forgotten), so there's never a flash of incorrect "nothing
  // selected" state in between.
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
      if (requestId !== latestRequestIdRef.current) return; // stale, discard
      setOptimisticIds(null); // real server state now loaded; stop overriding it
    },
  });

  const [searchQuery, setSearchQuery] = useState("");
  const [activeInstrument, setActiveInstrument] = useState<(typeof INSTRUMENT_TYPES)[number]>("All");
  const [activeSubtier, setActiveSubtier] = useState<(typeof SUBTIERS)[number]>("All");

  const heldIds = useMemo(
    () => new Set(optimisticIds ?? selected?.selected_scheme_ids ?? []),
    [optimisticIds, selected]
  );

  const filteredSchemes = useMemo(() => {
    const schemes = data?.eligible_schemes ?? [];
    return schemes.filter((scheme) => {
      const matchesSearch =
        scheme.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (scheme.category ?? "").toLowerCase().includes(searchQuery.toLowerCase());
      const matchesInstrument =
        activeInstrument === "All" || scheme.instrument_type === activeInstrument.toLowerCase();
      const matchesSubtier = activeSubtier === "All" || scheme.subtier === activeSubtier;
      return matchesSearch && matchesInstrument && matchesSubtier;
    });
  }, [data, searchQuery, activeInstrument, activeSubtier]);

  function toggle(schemeId: string) {
    const current = optimisticIds ?? selected?.selected_scheme_ids ?? [];
    const next = current.includes(schemeId)
      ? current.filter((id) => id !== schemeId)
      : [...current, schemeId];
    setOptimisticIds(next); // synchronous -- the very next click sees this, not stale cache data
    requestIdRef.current += 1;
    latestRequestIdRef.current = requestIdRef.current;
    toggleMutation.mutate({ nextIds: next, requestId: requestIdRef.current });
  }

  if (isLoading) return <LoadingState label="Loading eligible schemes…" />;
  if (isError) return <QueryError error={error} />;
  if (!data) return null;

  return (
    <div className="space-y-8">
      {/* Page Header */}
      <PageHeader
        eyebrow="Phase 2 · Discovery"
        title={`${data.eligible_count} schemes available to you`}
        description={`Out of all ${data.total_schemes} real Nippon India schemes. Risk is disclosed, never hidden — ${data.filter_summary.risk_flagged} carry a specific comfort-level warning below.`}
        action={<DemoBadge />}
      />

      {data.filter_summary.closed_to_purchase > 0 && (
        <div className="border border-line bg-paper-tint/30 px-4 py-3 text-xs text-ink-soft font-mono">
          {data.filter_summary.closed_to_purchase} scheme(s) are currently closed to new purchase
          (capacity management or regulatory caps) and excluded from this list.
        </div>
      )}

      {/* Filter and Search Controls */}
      <div className="border border-line p-5 md:p-6 bg-paper space-y-4 select-none">
        <div className="flex flex-col md:flex-row gap-4 justify-between items-start md:items-center">

          <div className="relative w-full md:max-w-xs">
            <input
              type="text"
              placeholder="Search schemes or category..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full border border-line bg-paper pl-9 pr-3.5 py-2 text-xs text-ink placeholder:text-ink-soft/40 focus:outline-none focus:border-crimson transition-colors"
            />
            <Search className="w-3.5 h-3.5 text-ink-soft absolute left-3 top-2.5" />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-soft font-mono mr-2">Type:</span>
            <div className="flex gap-1 border border-line p-0.5 bg-paper-tint/10">
              {INSTRUMENT_TYPES.map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveInstrument(t)}
                  className={`px-3 py-1 text-[10px] font-mono uppercase tracking-wider transition-colors cursor-pointer ${
                    activeInstrument === t ? "bg-ink text-paper" : "text-ink-soft hover:text-ink"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-soft font-mono mr-2">Subtier:</span>
            <div className="flex gap-1 border border-line p-0.5 bg-paper-tint/10">
              {SUBTIERS.map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveSubtier(t)}
                  className={`px-3 py-1 text-[10px] font-mono uppercase tracking-wider transition-colors cursor-pointer ${
                    activeSubtier === t ? "bg-crimson text-paper" : "text-ink-soft hover:text-crimson"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

        </div>
      </div>

      {/* Grid of Scheme Cards */}
      {filteredSchemes.length === 0 ? (
        <div className="border border-line border-dashed p-12 text-center select-none bg-paper-tint/10">
          <p className="font-mono text-xs text-ink-soft uppercase tracking-wider mb-2">No matching schemes located</p>
          <p className="text-sm text-ink-soft/75 max-w-md mx-auto">
            Try adjusting your search query or removing filters.
          </p>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          <AnimatePresence mode="popLayout">
            {filteredSchemes.map((scheme, index) => {
              const isHeld = heldIds.has(scheme.id);

              return (
                <motion.div
                  key={scheme.id}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  transition={{ duration: 0.35, delay: Math.min(index * 0.03, 0.4) }}
                  className={`border p-5 bg-paper flex flex-col justify-between group relative transition-all duration-200 ${
                    isHeld ? "border-accent" : "border-line hover:border-ink-soft"
                  }`}
                >
                  {/* Top: Badges */}
                  <div className="flex justify-between items-start gap-4 mb-3 select-none">
                    <div className="flex gap-1.5 flex-wrap">
                      <span className="font-mono text-[9px] uppercase tracking-wider font-semibold border border-line bg-paper-tint/30 px-2 py-0.5 text-ink-soft">
                        {scheme.category ?? scheme.instrument_type}
                      </span>
                    </div>

                    {scheme.subtier && (
                      <span className={`font-mono text-[9px] uppercase tracking-wider font-bold px-2 py-0.5 ${
                        scheme.subtier === "Core" ? "bg-ink text-paper" :
                        scheme.subtier === "Growth" ? "bg-crimson text-paper" :
                        "bg-crimson-bright text-paper"
                      }`}>
                        {scheme.subtier}
                      </span>
                    )}
                  </div>

                  {/* Fund Name */}
                  <div className="mb-4">
                    <Link
                      href={`/schemes/${scheme.id}`}
                      className="font-display text-lg font-semibold tracking-tight text-ink hover:text-crimson transition-colors block leading-snug"
                    >
                      {scheme.name}
                    </Link>
                  </div>

                  {/* NAV and AUM parameters */}
                  <div className="grid grid-cols-2 gap-4 border-t border-b border-line/50 py-3 mb-4 font-mono text-[11px] text-ink-soft select-none">
                    <div>
                      <span className="block uppercase tracking-wide text-[9px] font-semibold text-ink-soft/60">NAV price</span>
                      <span className="text-sm font-bold text-ink tabular-nums">
                        {scheme.nav_regular_growth ? `₹${scheme.nav_regular_growth.toFixed(2)}` : "—"}
                      </span>
                    </div>
                    <div>
                      <span className="block uppercase tracking-wide text-[9px] font-semibold text-ink-soft/60">AUM corpus</span>
                      <span className="text-sm font-bold text-ink tabular-nums">
                        {scheme.aum_cr ? `₹${scheme.aum_cr.toLocaleString("en-IN")} Cr` : "—"}
                      </span>
                    </div>
                  </div>

                  {/* Bottom Panel Actions & Risk Indicator */}
                  <div className="space-y-4">

                    {/* Real, backend-computed risk disclosure -- never a client-side guess */}
                    <div className="text-[10px] leading-relaxed font-sans select-none">
                      {scheme.risk_flag ? (
                        <span className="text-crimson font-semibold flex items-start gap-1">
                          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                          <span>{scheme.risk_flag}</span>
                        </span>
                      ) : (
                        <span className="text-success font-semibold flex items-center gap-1">
                          <Check className="w-3.5 h-3.5 shrink-0" />
                          Matches your stated risk comfort
                        </span>
                      )}
                    </div>

                    <div className="flex gap-2">
                      <Link
                        href={`/schemes/${scheme.id}`}
                        className="flex-1 border border-line text-center py-2 text-xs font-mono font-bold uppercase hover:bg-paper-tint/30 transition-colors"
                      >
                        Inspect Factsheet
                      </Link>

                      <button
                        onClick={() => toggle(scheme.id)}
                        className={`px-3 py-2 text-xs font-mono font-bold uppercase transition-colors cursor-pointer flex justify-center items-center ${
                          isHeld
                            ? "bg-crimson text-paper hover:bg-crimson-deep"
                            : "border border-ink hover:bg-ink hover:text-paper"
                        }`}
                        title={isHeld ? "Remove from analyzed portfolio" : "Add to analyzed portfolio"}
                      >
                        {isHeld ? <Minus className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                      </button>
                    </div>

                  </div>

                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      )}

    </div>
  );
}
