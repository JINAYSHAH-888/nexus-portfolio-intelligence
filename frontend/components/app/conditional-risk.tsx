import React from "react";
import { DataPanel, StatusChip, type Tone } from "@/components/shared";
import type { ConditionalRisk, FundVarRow, RiskValidationV2, VarBacktestRow } from "@/lib/api-types";

const pct = (v: number | undefined, d = 2) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);

const REGIME: Record<string, { tone: Tone; text: string }> = {
  calm: { tone: "ok", text: "Calmer than usual" },
  normal: { tone: "default", text: "Normal" },
  elevated: { tone: "warn", text: "Elevated" },
};

/**
 * Risk that tracks the current volatility regime: GARCH(1,1) supplies when
 * volatility is high, the portfolio's own standardised history supplies the
 * tail shape (filtered historical simulation). Shown with the out-of-sample
 * evidence that it, unlike plain historical VaR, passes both backtests.
 */
export function ConditionalRiskPanel({ cr }: { cr: ConditionalRisk }) {
  const regime = REGIME[cr.regime ?? "normal"];
  const ratio = cr.current_vol_annual && cr.long_run_vol_annual ? cr.current_vol_annual / cr.long_run_vol_annual : 1;
  const nifty = cr.validation?.["0.95"];

  return (
    <section className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-12">
        <div className="ink-panel ticked space-y-5 p-6 lg:col-span-5">
          <div className="flex items-center justify-between gap-3">
            <span className="label">Risk right now · {cr.validation_v2 ? "GARCH + HAR-X" : "GARCH"}</span>
            <StatusChip tone={regime.tone}>{regime.text}</StatusChip>
          </div>
          <div>
            <p className="label">A bad day (1 in 20)</p>
            <p data-numeric className="mt-1 font-display text-display-2 font-semibold leading-none text-accent-text">
              {pct(cr.var_95_1d)}
            </p>
            <p className="mt-2 text-step--1 text-text-secondary">
              On the worst 5% of days, the average loss is {pct(cr.es_95_1d)}.
            </p>
          </div>
          <div className="space-y-2 border-t border-line pt-4">
            <div className="flex justify-between font-mono text-step--2">
              <span className="text-text-secondary">Volatility now</span>
              <span data-numeric className="text-text">{pct(cr.current_vol_annual, 1)} a year</span>
            </div>
            <div className="relative h-1.5 bg-[var(--ink-line)]" aria-hidden="true">
              <div className="absolute inset-y-0 left-0 bg-[var(--ink-accent)]" style={{ width: `${Math.min(100, ratio * 50)}%` }} />
              <div className="absolute -inset-y-1 left-1/2 w-px bg-[var(--ink-text)]" />
            </div>
            <div className="flex justify-between font-mono text-step--2 text-text-muted">
              <span>long-run {pct(cr.long_run_vol_annual, 1)} ↑</span>
              {cr.half_life_days != null && <span>shocks halve in {cr.half_life_days} days</span>}
            </div>
            {cr.beta_to_nifty != null && (
              <p className="font-mono text-step--2 text-text-muted">
                Beta to Nifty {cr.beta_to_nifty.toFixed(2)} · market forecast as of {cr.market_forecast_as_of}
              </p>
            )}
          </div>
        </div>

        <div className="lg:col-span-7">
          <DataPanel
            label="Loss you should be ready for"
            note={cr.tail_99 === "evt" ? "1 in 20: filtered historical · 1 in 100: extreme-value tail" : "filtered historical simulation"}
          >
            <table className="w-full border-collapse text-step--1">
              <thead>
                <tr className="border-b border-line">
                  <th className="p-4 text-left font-normal"><span className="label">Horizon</span></th>
                  <th className="p-4 text-right font-normal"><span className="label">1 in 20</span></th>
                  <th className="p-4 text-right font-normal"><span className="label">Avg beyond</span></th>
                  <th className="p-4 text-right font-normal"><span className="label">1 in 100</span></th>
                  <th className="p-4 text-right font-normal"><span className="label">Avg beyond</span></th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["1 day", cr.var_95_1d, cr.es_95_1d, cr.var_99_1d, cr.es_99_1d],
                  ["10 days", cr.var_95_10d, cr.es_95_10d, cr.var_99_10d, cr.es_99_10d],
                ].map(([h, a, b, c, d]) => (
                  <tr key={h as string} className="border-b border-line last:border-0">
                    <td className="p-4 text-text">{h}</td>
                    {[a, b, c, d].map((v, i) => (
                      <td key={i} data-numeric className={`p-4 text-right ${i % 2 ? "text-text-secondary" : "text-text"}`}>
                        {pct(v as number)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t border-line px-5 py-3 text-step--2 leading-relaxed text-text-muted">
              Ten-day figures let volatility drift back toward its long-run level, as the model expects, instead of
              scaling today&apos;s by √10.
            </p>
          </DataPanel>
        </div>
      </div>

      {cr.validation_v2 && <EvidenceV2 v={cr.validation_v2} />}

      {!cr.validation_v2 && nifty && (
        <DataPanel label="Why this model: 15 years of out-of-sample VaR" note={`${nifty.garch_fhs.days.toLocaleString("en-IN")} days · Nifty 50`}>
          <table className="w-full border-collapse text-step--1">
            <thead>
              <tr className="border-b border-line">
                <th className="p-4 text-left font-normal"><span className="label">Method</span></th>
                <th className="p-4 text-right font-normal"><span className="label">Breach rate (want 5%)</span></th>
                <th className="p-4 text-right font-normal"><span className="label">Right count?</span></th>
                <th className="p-4 text-right font-normal"><span className="label">Not clustered?</span></th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["GARCH + filtered historical (used)", nifty.garch_fhs],
                  ["GARCH, Student-t", nifty.garch_t],
                  ["Historical simulation", nifty.historical],
                ] as [string, VarBacktestRow][]
              ).map(([name, row]) => (
                <tr key={name} className={`border-b border-line last:border-0 ${name.includes("used") ? "bg-bg-subtle" : ""}`}>
                  <td className="p-4 text-text">{name}</td>
                  <td data-numeric className="p-4 text-right text-text">{pct(row.breach_rate ?? undefined)}</td>
                  <td className="p-4 text-right"><Verdict p={row.kupiec_p} /></td>
                  <td className="p-4 text-right"><Verdict p={row.independence_p} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="border-t border-line px-5 py-3 text-step--2 leading-relaxed text-text-muted">
            Each day&apos;s VaR used only the 750 days before it. Kupiec tests the number of breaches; Christoffersen
            tests whether they bunch together — plain historical VaR misses exactly when volatility jumps.
          </p>
        </DataPanel>
      )}
    </section>
  );
}

function Verdict({ p }: { p: number }) {
  const pass = p >= 0.05;
  return (
    <StatusChip tone={pass ? "ok" : "err"}>
      {pass ? "Pass" : "Fail"} · p {p.toFixed(3)}
    </StatusChip>
  );
}

const VOL_NAMES: Record<string, string> = {
  garch_t: "GARCH(1,1), Student-t (previous)",
  gjr_t: "GJR-GARCH (falls weigh more)",
  har: "HAR realised volatility",
  har_x: "HAR-X + India VIX, S&P 500, USD/INR, crude (used)",
};
const VAR_NAMES: Record<string, string> = {
  garch_fhs: "Fund GARCH + filtered historical (previous)",
  harx_factor: "HAR-X mapped by beta",
  blend: "Blend: fund GARCH + HAR-X (used)",
};

function EvidenceV2({ v }: { v: RiskValidationV2 }) {
  const q1 = v.volatility_forecast_qlike["1d"];
  const q5 = v.volatility_forecast_qlike["5d"];
  const dm = v.diebold_mariano_garch_vs_harx["1d"];
  const best = Math.min(...Object.values(q1));
  const fb = v.fund_var_backtest;
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <DataPanel label="Which volatility forecast is most accurate" note={`QLIKE, lower is better · ${dm?.n_days.toLocaleString("en-IN")} days`}>
        <table className="w-full border-collapse text-step--1">
          <thead>
            <tr className="border-b border-line">
              <th className="p-4 text-left font-normal"><span className="label">Model</span></th>
              <th className="p-4 text-right font-normal"><span className="label">1 day</span></th>
              <th className="p-4 text-right font-normal"><span className="label">5 days</span></th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(q1).map((k) => (
              <tr key={k} className={`border-b border-line last:border-0 ${q1[k] === best ? "bg-bg-subtle" : ""}`}>
                <td className="p-4 text-text">{VOL_NAMES[k] ?? k}</td>
                <td data-numeric className="p-4 text-right text-text">{q1[k].toFixed(3)}</td>
                <td data-numeric className="p-4 text-right text-text-secondary">{q5[k]?.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t border-line px-5 py-3 text-step--2 leading-relaxed text-text-muted">
          Nifty 50, 2013 onward, every forecast made only with data available that day; target = realised variance
          from daily high/low/open/close. HAR-X beats GARCH by {(((q1.garch_t - q1.har_x) / q1.garch_t) * 100).toFixed(0)}%
          {dm ? ` — Diebold–Mariano p ${dm.p < 0.0001 ? "< 0.0001" : dm.p.toFixed(4)}, not luck.` : "."}
        </p>
      </DataPanel>

      <DataPanel label="VaR & ES on 13 real funds" note={`${fb["0.95"].blend.days.toLocaleString("en-IN")} fund-days`}>
        <table className="w-full border-collapse text-step--1">
          <thead>
            <tr className="border-b border-line">
              <th className="p-4 text-left font-normal"><span className="label">Method · level</span></th>
              <th className="p-4 text-right font-normal"><span className="label">Breaches</span></th>
              <th className="p-4 text-right font-normal"><span className="label">Tests passed</span></th>
              <th className="p-4 text-right font-normal"><span className="label">FZ0 score</span></th>
            </tr>
          </thead>
          <tbody>
            {(["0.95", "0.99"] as const).flatMap((lvl) =>
              (Object.entries(fb[lvl]) as [string, FundVarRow][]).map(([k, row]) => (
                <tr key={lvl + k} className={`border-b border-line last:border-0 ${k === "blend" ? "bg-bg-subtle" : ""}`}>
                  <td className="p-4 text-text">
                    {VAR_NAMES[k] ?? k} <span className="font-mono text-step--2 text-text-muted">· {lvl === "0.95" ? "95%" : "99%"}</span>
                  </td>
                  <td data-numeric className="p-4 text-right text-text">{(row.mean_breach_rate * 100).toFixed(2)}%</td>
                  <td data-numeric className="whitespace-nowrap p-4 text-right text-text-secondary">
                    {row.kupiec_pass}/{row.funds} · {row.independence_pass}/{row.funds}
                  </td>
                  <td data-numeric className="p-4 text-right text-text">{row.mean_fz0.toFixed(3)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
        <p className="border-t border-line px-5 py-3 text-step--2 leading-relaxed text-text-muted">
          Tests passed = right number of breaches · breaches not clustered. FZ0 scores VaR and ES together (lower is
          better). Rule: the lowest FZ0 among methods that pass both tests for at least 90% of funds.
        </p>
      </DataPanel>
    </div>
  );
}
