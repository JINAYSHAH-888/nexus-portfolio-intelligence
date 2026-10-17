"use client";

import React, { useState, useMemo, useRef } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getFilteredSchemes, getSelectedSchemes, selectSchemes } from "@/lib/api-client";
import { PageHeader, DemoBadge } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import { Plus, Check, Search, AlertTriangle } from "lucide-react";

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
      <PageHeader
        eyebrow="Discover"
        title={`${data.eligible_count} schemes available to you`}
        description={`Out of all ${data.total_schemes} real Nippon India schemes. Risk is disclosed, never hidden — ${data.filter_summary.risk_flagged} carry a comfort-level warning.`}
        action={<DemoBadge />}
      />

      {data.filter_summary.closed_to_purchase > 0 && (
        <p className="border-l-2 border-accent bg-bg-subtle px-4 py-3 font-mono text-step--2 text-text-secondary">
          {data.filter_summary.closed_to_purchase} scheme(s) are closed to new purchase (capacity or regulatory caps)
          and excluded from this list.
        </p>
      )}

      {/* Controls */}
      <div className="glass flex flex-col gap-4 p-4 md:flex-row md:items-center md:justify-between [&>*]:relative [&>*]:z-[2]">
        <label className="relative block w-full md:max-w-sm">
          <span className="sr-only">Search schemes</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" aria-hidden="true" />
          <input
            type="search"
            placeholder="Search schemes or category"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="field w-full pl-9"
          />
        </label>
        <div className="flex flex-wrap items-center gap-4">
          <Segmented label="Type" options={INSTRUMENT_TYPES} value={activeInstrument} onChange={setActiveInstrument} />
          <Segmented label="Risk tier" options={SUBTIERS} value={activeSubtier} onChange={setActiveSubtier} />
        </div>
      </div>

      <p className="font-mono text-step--2 text-text-muted" aria-live="polite">
        Showing {filteredSchemes.length} · {heldIds.size} in your portfolio
      </p>

      {filteredSchemes.length === 0 ? (
        <div className="ticked border border-dashed border-line bg-bg p-12 text-center">
          <p className="label mb-2">No matching schemes</p>
          <p className="text-step--1 text-text-secondary">Try a different search or clear a filter.</p>
        </div>
      ) : (
        <ul className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {filteredSchemes.map((scheme, index) => {
            const isHeld = heldIds.has(scheme.id);
            const warn = scheme.risk_flag_level === "strong" ? "text-accent-text" : "text-warn";
            return (
              <li
                key={scheme.id}
                className={`enter group flex flex-col ${isHeld ? "ink-panel" : "border border-line bg-bg hover:border-line-strong"} transition-colors`}
                style={{ "--d": `${Math.min(index * 30, 400)}ms` } as React.CSSProperties}
              >
                <div className="flex items-start justify-between gap-3 px-5 pt-5">
                  <span className="label">{scheme.category ?? scheme.instrument_type}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    {isHeld && <span className="label text-accent-text">In portfolio</span>}
                    {scheme.subtier && (
                      <span className="border border-line px-2 py-0.5 font-mono text-step--2 uppercase tracking-wider text-text-secondary">
                        {scheme.subtier}
                      </span>
                    )}
                  </div>
                </div>

                <Link
                  href={`/schemes/${scheme.id}`}
                  className="mt-3 block px-5 font-display text-step-2 font-semibold leading-snug tracking-tight text-text underline-offset-4 hover:underline"
                >
                  {scheme.name.replace(/^Nippon India /, "")}
                </Link>

                <dl className="mx-5 mt-5 grid grid-cols-3 gap-3 border-y border-line py-3">
                  <Fact k="NAV" v={scheme.nav_regular_growth ? `₹${scheme.nav_regular_growth.toFixed(2)}` : "—"} />
                  <Fact k="AUM" v={scheme.aum_cr ? `₹${Math.round(scheme.aum_cr).toLocaleString("en-IN")} Cr` : "—"} />
                  <Fact k="Expense" v={scheme.expense_ratio_regular != null ? `${scheme.expense_ratio_regular.toFixed(2)}%` : "—"} />
                </dl>

                <p className="flex flex-1 items-start gap-2 px-5 py-4 text-step--2 leading-relaxed">
                  {scheme.risk_flag ? (
                    <>
                      <AlertTriangle className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${warn}`} aria-hidden="true" />
                      <span className="text-text-secondary">{scheme.risk_flag}</span>
                    </>
                  ) : (
                    <>
                      <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" aria-hidden="true" />
                      <span className="text-text-secondary">Matches your stated risk comfort</span>
                    </>
                  )}
                </p>

                <div className="mt-auto flex gap-2 px-5 pb-5">
                  <Link href={`/schemes/${scheme.id}`} className="btn flex-1">
                    Factsheet
                  </Link>
                  <button
                    type="button"
                    onClick={() => toggle(scheme.id)}
                    aria-pressed={isHeld}
                    className={isHeld ? "btn border-[var(--ink-accent)] text-[var(--ink-accent)]" : "btn btn-primary"}
                  >
                    {isHeld ? (
                      "Remove"
                    ) : (
                      <>
                        <Plus className="h-4 w-4" aria-hidden="true" /> Add
                      </>
                    )}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="label">{k}</dt>
      <dd data-numeric className="mt-1 truncate text-step--1 font-medium text-text">
        {v}
      </dd>
    </div>
  );
}

function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex items-center gap-2">
      <span className="label hidden sm:inline">{label}</span>
      <div className="flex rounded-full border border-line bg-bg p-0.5">
        {options.map((o) => (
          <button
            key={o}
            type="button"
            role="radio"
            aria-checked={value === o}
            onClick={() => onChange(o)}
            className={`min-h-[36px] rounded-full px-3.5 font-mono text-step--2 uppercase tracking-wider transition-colors ${
              value === o ? "bg-[var(--mark)] text-[var(--mark-fg)]" : "text-text-secondary hover:text-text"
            }`}
          >
            {o}
          </button>
        ))}
      </div>
    </div>
  );
}
