"use client";

import Link from "next/link";
import type { CSSProperties, ComponentType, ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Gauge, LineChart, Network, ShieldAlert } from "lucide-react";
import { getDashboard, getOverlap, getPlos, getRisk, runArise } from "@/lib/api-client";
import { PageHeader, StatusChip, type Tone } from "@/components/shared";
import { QueryError, LoadingState } from "@/components/query-state";
import GradientCard from "@/components/landing/gradient-card";
import ScrollText from "@/components/landing/scroll-text";
import { formatINR, formatProbability, formatProbabilityRange, probabilityParts, schemeShortName } from "@/lib/utils";

const d = (s: number) => ({ "--d": `${s}s` }) as CSSProperties;

/* Segment colours for the allocation bar: brand steps for equity, a neutral
 * for debt, so the bar reads "how much is in stocks" at a glance. */
const EQUITY_STEPS = ["var(--brand-9)", "var(--brand-11)", "var(--brand-7)", "var(--brand-10)", "var(--brand-12)"];
const DEBT_STEP = "var(--n-8)";

const STRATEGY: Record<string, string> = {
  guardrails: "flexible spending",
  adaptive: "flexible spending",
  robotic: "fixed spending",
  vpw: "variable withdrawals",
  ratchet: "ratchet spending",
};

/**
 * Overview: the four readings that answer "how is my portfolio doing?", each
 * linking to its full screen, then the portfolio itself.
 *
 * Every figure is live engine output. Each card loads on its own, so the
 * slow ones (risk and ARISE take ~2s) never hold the rest of the page back,
 * and one failing engine shows as one unavailable card, not a broken page.
 */
export default function DashboardPage() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: getDashboard });
  const hasPortfolio = (dash.data?.portfolio.scheme_count ?? 0) > 0;
  const overlap = useQuery({ queryKey: ["overlap"], queryFn: getOverlap, enabled: hasPortfolio, retry: 0 });
  const plos = useQuery({ queryKey: ["plos"], queryFn: getPlos, enabled: hasPortfolio, retry: 0 });
  const risk = useQuery({ queryKey: ["risk"], queryFn: getRisk, enabled: hasPortfolio, retry: 0 });
  const arise = useQuery({
    queryKey: ["arise", "default"],
    queryFn: () => runArise({}),
    enabled: hasPortfolio,
    retry: 0,
    staleTime: 5 * 60_000,
  });

  if (dash.isLoading) return <LoadingState label="Loading your overview…" />;
  if (dash.isError) return <QueryError error={dash.error} />;
  if (!dash.data) return null;

  const { investor, portfolio, market } = dash.data;
  const first = investor.name?.split(" ")[0];
  const schemesLabel = `${portfolio.scheme_count} ${portfolio.scheme_count === 1 ? "scheme" : "schemes"}`;
  const subline = [
    investor.years_to_retirement != null ? `${investor.years_to_retirement} years to retirement` : null,
    schemesLabel,
    `${formatINR(portfolio.invested_amount)} invested`,
  ]
    .filter(Boolean)
    .join(" · ");

  if (!hasPortfolio) {
    return (
      <>
        <PageHeader eyebrow="Overview" title={`Welcome${first ? `, ${first}` : ""}.`} description={subline} />
        <div className="enter ticked max-w-2xl border border-line bg-bg p-8">
          <h2 className="font-display text-step-3 font-semibold text-text">No portfolio yet</h2>
          <p className="mt-2 text-step-0 text-text-secondary">
            Pick the schemes you hold and every reading on this page fills in: overlap, diversification, risk and
            your retirement odds.
          </p>
          <Link href="/schemes" className="btn btn-primary mt-6">
            Choose schemes <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </div>
      </>
    );
  }

  /* ── readings ─────────────────────────────────────────────────────────── */
  const topPair = overlap.data
    ? [...overlap.data.edges].sort((a, b) => b.weighted_overlap - a.weighted_overlap)[0]
    : undefined;
  const nameOf = (id: string) =>
    schemeShortName(overlap.data?.nodes.find((n) => n.id === id)?.name ?? id).replace(/ Fund$/, "");
  const heavyPairs = overlap.data?.edges.filter((e) => e.weighted_overlap >= 0.3).length ?? 0;

  const plosVerdict = plos.data?.interpretation.split("—")[0].trim() ?? "";
  const plosTone: Tone = /high/i.test(plosVerdict) ? "err" : /moderate/i.test(plosVerdict) ? "warn" : "ok";

  const backtest = risk.data?.portfolio_var_backtest_95;
  const synthetic = risk.data?.data_source.includes("synthetic");

  // The estimate, never the raw share of surviving paths (see probabilityParts).
  const success = arise.data ? (arise.data.success_rate_estimate ?? arise.data.adaptive_success_rate) : undefined;
  const successParts = success != null ? probabilityParts(success) : null;
  const fixedSuccess = arise.data ? (arise.data.robotic_success_estimate ?? arise.data.robotic_success_rate) : undefined;

  return (
    <>
      <PageHeader eyebrow="Overview" title={`Welcome back${first ? `, ${first}` : ""}.`} description={subline} />

      <section aria-label="Portfolio readings" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Reading
          d={0.04}
          href="/analysis/overlap"
          icon={Network}
          label="Overlap"
          query={overlap}
          value={topPair ? Math.round(topPair.weighted_overlap * 100) : null}
          unit="%"
          caption={topPair ? `Same money · ${nameOf(topPair.source_id)} × ${nameOf(topPair.target_id)}` : "No overlapping pair"}
          chip={
            topPair
              ? topPair.weighted_overlap >= 0.3
                ? { tone: "err", text: "High overlap" }
                : topPair.weighted_overlap >= 0.15
                  ? { tone: "warn", text: "Moderate" }
                  : { tone: "ok", text: "Low" }
              : { tone: "ok", text: "None" }
          }
          note={
            heavyPairs > 0
              ? `${heavyPairs} fund ${heavyPairs === 1 ? "pair shares" : "pairs share"} more than 30% of their money.`
              : "No pair shares more than 30% of its money."
          }
        />
        <Reading
          d={0.1}
          href="/analysis/plos"
          icon={Gauge}
          label="Diversification"
          query={plos}
          value={plos.data ? Math.round(plos.data.diversification_score) : null}
          unit="/100"
          caption="PLOS diversification score"
          chip={{ tone: plosTone, text: plosVerdict || "Scored" }}
          note="Ten layers, from real return correlation to valuation. Higher is more diversified."
        />
        <Reading
          d={0.16}
          href="/analysis/risk"
          icon={ShieldAlert}
          label="Risk"
          query={risk}
          value={risk.data ? Number((risk.data.portfolio_volatility * 100).toFixed(1)) : null}
          unit="%"
          caption="Volatility, a year"
          chip={
            backtest
              ? backtest.passed
                ? { tone: "ok", text: "VaR backtest passed" }
                : { tone: "err", text: "VaR backtest failed" }
              : { tone: "default", text: "Backtest unavailable" }
          }
          note={
            synthetic
              ? "Modelled on calibrated synthetic prices: live market data is switched off."
              : `${risk.data?.n_observations ?? "—"} trading days of real price history.`
          }
          caution={synthetic}
        />
        <Reading
          d={0.22}
          href="/analysis/arise"
          icon={LineChart}
          label="Retirement"
          query={arise}
          loadingLabel="Simulating 1,000 lifetimes…"
          value={successParts?.value ?? null}
          prefix={successParts?.prefix}
          unit="%"
          caption={`Plan success · ${STRATEGY[arise.data?.primary_strategy ?? ""] ?? "primary strategy"}`}
          chip={
            success == null
              ? { tone: "default", text: "—" }
              : success >= 0.85
                ? { tone: "ok", text: "On track" }
                : success >= 0.7
                  ? { tone: "warn", text: "Fragile" }
                  : { tone: "err", text: "At risk" }
          }
          note={
            arise.data && fixedSuccess != null
              ? `${
                  arise.data.success_rate_low != null && arise.data.success_rate_high != null
                    ? `Likely ${formatProbabilityRange(arise.data.success_rate_low, arise.data.success_rate_high)}. `
                    : ""
                }${formatProbability(fixedSuccess)} if spending never adjusts.`
              : ""
          }
        />
      </section>

      <section aria-labelledby="portfolio-title" className="mt-24">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <ScrollText
            id="portfolio-title"
            text="Your portfolio"
            className="font-display text-display-2 font-semibold text-text"
          />
          {market.data_delayed && <p className="label">Prices delayed, not live</p>}
        </div>

        {/* Allocation bar: proportion of money, equity in brand reds, debt in neutral. */}
        <div className="enter mt-8 flex h-3 w-full overflow-hidden" style={d(0.1)} role="img"
          aria-label={portfolio.holdings
            .map((h) => `${schemeShortName(String(h.scheme_name))} ${Math.round(Number(h.weight) * 100)}%`)
            .join(", ")}
        >
          {portfolio.holdings.map((h, i) => (
            <span
              key={String(h.scheme_id)}
              className="h-full border-r-2 border-bg last:border-r-0"
              style={{
                width: `${Number(h.weight) * 100}%`,
                background: h.instrument_type === "debt" ? DEBT_STEP : EQUITY_STEPS[i % EQUITY_STEPS.length],
              }}
            />
          ))}
        </div>

        <div className="enter mt-6 overflow-x-auto" style={d(0.16)}>
          <table className="w-full min-w-[640px] text-step-0">
            <caption className="sr-only">Holdings in your portfolio, with allocation and latest NAV</caption>
            <thead>
              <tr className="border-b border-line-strong text-left">
                <th scope="col" className="py-3 pr-4 font-normal"><span className="label">Scheme</span></th>
                <th scope="col" className="py-3 pr-4 font-normal"><span className="label">Type</span></th>
                <th scope="col" className="py-3 pr-4 text-right font-normal"><span className="label">Weight</span></th>
                <th scope="col" className="py-3 pr-4 text-right font-normal"><span className="label">Amount</span></th>
                <th scope="col" className="py-3 text-right font-normal"><span className="label">NAV</span></th>
              </tr>
            </thead>
            <tbody>
              {portfolio.holdings.map((h, i) => (
                <tr key={String(h.scheme_id)} className="row-interactive border-b border-line">
                  <th scope="row" className="py-4 pr-4 text-left font-sans font-medium text-text">
                    <span className="flex items-center gap-3">
                      <span
                        aria-hidden="true"
                        className="h-2.5 w-2.5 shrink-0"
                        style={{
                          background: h.instrument_type === "debt" ? DEBT_STEP : EQUITY_STEPS[i % EQUITY_STEPS.length],
                        }}
                      />
                      <Link href={`/schemes/${String(h.scheme_id)}`} className="link-wipe">
                        {schemeShortName(String(h.scheme_name))}
                      </Link>
                    </span>
                  </th>
                  <td className="py-4 pr-4 font-sans capitalize text-text-secondary">{String(h.instrument_type)}</td>
                  <td className="py-4 pr-4 text-right text-text">{(Number(h.weight) * 100).toFixed(0)}%</td>
                  <td className="py-4 pr-4 text-right text-text">{formatINR(Number(h.allocated_amount))}</td>
                  <td className="py-4 text-right text-text-secondary">₹{Number(h.nav).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <nav aria-label="Next steps" className="enter mt-24 grid gap-px border border-line bg-line sm:grid-cols-3" style={d(0.1)}>
        <NextStep href="/profile" title="Refine your profile" body="Income, horizon and SIP drive every projection." />
        <NextStep href="/schemes" title="Change your schemes" body="Add, remove or rebalance what you hold." />
        <NextStep href="/analysis/arise" title="Explore retirement" body="Strategies, spending models and the full ledger." />
      </nav>
    </>
  );
}

type QueryLike = { isLoading: boolean; isError: boolean; error: unknown; isFetching?: boolean };

function Reading({
  href,
  icon: Icon,
  label,
  query,
  value,
  prefix = "",
  unit,
  caption,
  chip,
  note,
  caution = false,
  loadingLabel = "Calculating…",
  d: delay,
}: {
  href: string;
  icon: ComponentType<{ className?: string; strokeWidth?: number; "aria-hidden"?: boolean }>;
  label: string;
  query: QueryLike;
  value: number | null;
  /** ">" or "<" for clamped probabilities -- never print 100% or 0%. */
  prefix?: string;
  unit: string;
  caption: string;
  chip: { tone: Tone; text: string };
  note: ReactNode;
  caution?: boolean;
  loadingLabel?: string;
  d: number;
}) {
  return (
    <Link href={href} className="enter group block h-full" style={d(delay)}>
      <GradientCard className="flex h-full flex-col p-6">
        <div className="flex items-center justify-between">
          <span className="label">{label}</span>
          <Icon className="h-5 w-5 text-accent-text" strokeWidth={1.5} aria-hidden />
        </div>

        {query.isLoading ? (
          <p className="mt-10 flex-1 font-mono text-step--1 text-text-muted" aria-live="polite">
            {loadingLabel}
          </p>
        ) : query.isError || value == null ? (
          <p className="mt-10 flex-1 text-step--1 text-text-secondary">
            Unavailable right now. Open the full screen for details.
          </p>
        ) : (
          <>
            <p className="mt-8 font-display text-display-2 font-semibold leading-none tabular-nums text-text">
              {prefix && <span className="mr-0.5 text-step-3 text-text-muted">{prefix}</span>}
              {value}
              <span className="ml-1 text-step-2 text-text-muted">{unit}</span>
            </p>
            <p className="mt-3 text-step--1 text-text-secondary">{caption}</p>
            <div className="mt-5">
              <StatusChip tone={chip.tone}>{chip.text}</StatusChip>
            </div>
            <p className={`mt-4 flex-1 text-step--1 leading-snug ${caution ? "text-warn" : "text-text-muted"}`}>
              {note}
            </p>
          </>
        )}

        <span className="mt-6 inline-flex items-center gap-1.5 font-mono text-step--2 uppercase tracking-wider text-text-secondary group-hover:text-accent-text">
          Open {label.toLowerCase()}
          <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
        </span>
      </GradientCard>
    </Link>
  );
}

function NextStep({ href, title, body }: { href: string; title: string; body: string }) {
  return (
    <Link href={href} className="group bg-bg p-6 transition-colors duration-150 hover:bg-bg-hover">
      <span className="flex items-center justify-between gap-3 font-display text-step-1 font-semibold text-text">
        {title}
        <ArrowRight className="h-4 w-4 text-accent-text transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
      </span>
      <span className="mt-1 block text-step--1 text-text-secondary">{body}</span>
    </Link>
  );
}
