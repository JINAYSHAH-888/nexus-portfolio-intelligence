"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { createProfile, getProfile, ApiRequestError } from "@/lib/api-client";
import { PageHeader, DemoBadge } from "@/components/shared";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import type { InvestmentGoal, RiskAppetite, ProfileResponse } from "@/lib/api-types";

const GOAL_OPTIONS: { label: string; value: InvestmentGoal }[] = [
  { label: "Growth", value: "growth" },
  { label: "Income", value: "income" },
  { label: "Safety", value: "safety" },
  { label: "Tax saving", value: "tax_saving" },
];

const RISK_OPTIONS: { label: string; value: RiskAppetite }[] = [
  { label: "Conservative", value: "conservative" },
  { label: "Moderate", value: "moderate" },
  { label: "Moderately High", value: "moderately_high" },
  { label: "Aggressive", value: "aggressive" },
];

export default function ProfileWizardPage() {
  // If the investor already has a profile, preload it so this doubles as
  // the edit flow too -- the backend treats POST /profile as safe to
  // resubmit (create-or-update), so one code path covers both cases.
  //
  // isFetched (not isLoading alone) is what we wait for here: it's true once
  // the FIRST fetch has settled, whether it resolved with real data or with
  // "no profile yet" (a new investor). Only THEN do we mount the actual form,
  // with its initial state computed once from that resolved value via a lazy
  // useState initializer -- not via setState calls inside an effect, which
  // cause cascading re-renders for no benefit (React's own lint rule flags
  // exactly this pattern).
  const { data: existing, isFetched } = useQuery({
    queryKey: ["profile"],
    queryFn: getProfile,
    retry: 0,
  });

  if (!isFetched) {
    return <p className="font-mono text-step--1 text-text-muted">Loading…</p>;
  }

  return <ProfileForm existing={existing ?? null} />;
}

function ProfileForm({ existing }: { existing: ProfileResponse | null }) {
  const router = useRouter();

  const [step, setStep] = useState(0);
  const [name, setName] = useState(existing?.full_name ?? "");
  const [dob, setDob] = useState(existing?.date_of_birth ?? "");
  const [occupation, setOccupation] = useState(existing?.occupation ?? "");
  const [income, setIncome] = useState(existing?.monthly_income ?? 150000);
  const [corpus, setCorpus] = useState(existing?.total_corpus ?? 2000000);
  const [investNow, setInvestNow] = useState(existing?.investment_amount ?? 500000);
  const [goal, setGoal] = useState<InvestmentGoal>(existing?.goal ?? "growth");
  const [riskAppetite, setRiskAppetite] = useState<RiskAppetite>(existing?.risk_appetite ?? "moderately_high");
  const [retirementAge, setRetirementAge] = useState(existing?.target_retirement_age ?? 60);
  const [expenses, setExpenses] = useState(existing?.monthly_expenses_in_retirement ?? 60000);
  const [sipAmount, setSipAmount] = useState<number | "">(existing?.monthly_sip ?? 25000);
  const [sipStepUp, setSipStepUp] = useState<number | "">(
    existing?.sip_step_up_pct != null ? existing.sip_step_up_pct * 100 : 10
  );
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const stepsList = [
    "About you",
    "Your finances",
    "Goal & risk",
    "Retirement",
    "SIP details"
  ];

  async function handleNext() {
    if (step < 4) {
      setStep(step + 1);
      return;
    }

    setError(null);
    setIsSaving(true);
    try {
      await createProfile({
        full_name: name,
        date_of_birth: dob,
        occupation,
        monthly_income: Number(income),
        total_corpus: Number(corpus),
        investment_amount: Number(investNow),
        goal,
        risk_appetite: riskAppetite,
        target_retirement_age: Number(retirementAge),
        monthly_expenses_in_retirement: Number(expenses),
        monthly_sip: sipAmount === "" ? undefined : Number(sipAmount),
        sip_step_up_pct: sipStepUp === "" ? undefined : Number(sipStepUp) / 100,
      });
      router.push("/schemes");
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.body.message : "Could not save your profile.");
    } finally {
      setIsSaving(false);
    }
  }

  const handleBack = () => {
    if (step > 0) {
      setStep(step - 1);
    }
  };

  const inr = (v: number | "") => (v === "" ? "—" : `₹${Number(v).toLocaleString("en-IN")}`);
  const summary: [string, string][] = [
    ["Name", name || "—"],
    ["Income / month", inr(income)],
    ["Corpus", inr(corpus)],
    ["Goal", GOAL_OPTIONS.find((g) => g.value === goal)?.label ?? goal],
    ["Risk", RISK_OPTIONS.find((r) => r.value === riskAppetite)?.label ?? riskAppetite],
    ["Retire at", `${retirementAge}`],
    ["SIP", sipAmount === "" ? "estimated" : `${inr(sipAmount)} · +${sipStepUp || 0}%/yr`],
  ];

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={existing ? "Your profile" : "Step 1 of the journey"}
        title={existing ? "Investor profile" : "Tell us about you"}
        description="Ten answers set the bounds for every analysis — which schemes suit you, and what your retirement projection simulates."
        action={<DemoBadge />}
      />

      <div className="grid items-start gap-6 lg:grid-cols-12">
        {/* Step rail + live summary */}
        <aside className="ink-panel ticked p-6 lg:sticky lg:top-28 lg:col-span-4">
          <ol className="space-y-1">
            {stepsList.map((label, idx) => {
              const state = idx < step ? "done" : idx === step ? "current" : "todo";
              return (
                <li key={label}>
                  <button
                    type="button"
                    onClick={() => idx <= step && setStep(idx)}
                    disabled={idx > step}
                    aria-current={state === "current" ? "step" : undefined}
                    className="flex min-h-[44px] w-full items-center gap-3 text-left disabled:cursor-default"
                  >
                    <span
                      className={`grid h-7 w-7 shrink-0 place-items-center rounded-full font-mono text-step--2 ${
                        state === "current"
                          ? "bg-[var(--ink-accent)] text-[var(--ink)]"
                          : state === "done"
                            ? "border border-[var(--ink-accent)] text-[var(--ink-accent)]"
                            : "border border-line text-text-muted"
                      }`}
                    >
                      {state === "done" ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : idx + 1}
                    </span>
                    <span className={state === "todo" ? "text-text-muted" : "font-medium text-text"}>{label}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          <dl className="mt-6 space-y-2 border-t border-line pt-5 font-mono text-step--2">
            {summary.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-text-muted">{k}</dt>
                <dd className="truncate text-right text-text">{v}</dd>
              </div>
            ))}
          </dl>
        </aside>

        {/* Active step */}
        <section className="ticked flex min-h-[460px] flex-col border border-line bg-bg p-6 md:p-8 lg:col-span-8">
          <div key={step} className="enter flex-1 space-y-6">
            {step === 0 && (
              <>
                <StepTitle n={1} title="About you" sub="Used for age-based eligibility and to personalise your screens." />
                <div className="grid gap-5 md:grid-cols-2">
                  <Field label="Full name">
                    <input type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className="field w-full" placeholder="e.g. Rajesh Kumar" />
                  </Field>
                  <Field label="Date of birth">
                    <input type="date" autoComplete="bday" value={dob} onChange={(e) => setDob(e.target.value)} className="field w-full" />
                  </Field>
                </div>
                <Field label="Occupation">
                  <input type="text" autoComplete="organization-title" value={occupation} onChange={(e) => setOccupation(e.target.value)} className="field w-full" placeholder="e.g. Salaried, business owner" />
                </Field>
              </>
            )}

            {step === 1 && (
              <>
                <StepTitle n={2} title="Your finances" sub="Monthly flows and existing assets, in rupees." />
                <div className="grid gap-5 md:grid-cols-2">
                  <Field label="Net monthly income (₹)">
                    <input type="number" inputMode="numeric" value={income} onChange={(e) => setIncome(Number(e.target.value))} className="field w-full font-mono" />
                  </Field>
                  <Field label="Total corpus (₹)">
                    <input type="number" inputMode="numeric" value={corpus} onChange={(e) => setCorpus(Number(e.target.value))} className="field w-full font-mono" />
                  </Field>
                </div>
                <Field label="Amount to invest now (₹)" error={investNow > corpus ? "Cannot exceed your total corpus." : undefined}>
                  <input type="number" inputMode="numeric" value={investNow} onChange={(e) => setInvestNow(Number(e.target.value))} className="field w-full font-mono" aria-invalid={investNow > corpus} />
                </Field>
              </>
            )}

            {step === 2 && (
              <>
                <StepTitle n={3} title="Goal & risk" sub="A mismatch never hides a scheme — it shows a specific warning on it instead." />
                <Choice label="Investment goal" options={GOAL_OPTIONS} value={goal} onChange={setGoal} />
                <Choice label="Risk appetite" options={RISK_OPTIONS} value={riskAppetite} onChange={setRiskAppetite} />
              </>
            )}

            {step === 3 && (
              <>
                <StepTitle n={4} title="Retirement" sub="When the projection switches from saving to spending." />
                <div className="space-y-3">
                  <div className="flex items-baseline justify-between">
                    <span className="label">Target retirement age</span>
                    <span data-numeric className="text-step-3 font-medium text-accent-text">{retirementAge}</span>
                  </div>
                  <input
                    type="range"
                    min="40"
                    max="80"
                    value={retirementAge}
                    onChange={(e) => setRetirementAge(Number(e.target.value))}
                    aria-label="Target retirement age"
                    className="h-1 w-full cursor-pointer accent-[var(--accent)]"
                  />
                  <div className="flex justify-between font-mono text-step--2 text-text-muted">
                    <span>40</span>
                    <span>60 · typical</span>
                    <span>80</span>
                  </div>
                </div>
                <Field label="Monthly expenses in retirement, today's money (₹)">
                  <input type="number" inputMode="numeric" value={expenses} onChange={(e) => setExpenses(Number(e.target.value))} className="field w-full font-mono" />
                </Field>
              </>
            )}

            {step === 4 && (
              <>
                <StepTitle n={5} title="Monthly SIP" sub="Optional. Leave it blank and ARISE estimates 20% of income — and labels it as an estimate." />
                <div className="grid gap-5 md:grid-cols-2">
                  <Field
                    label="Monthly SIP (₹)"
                    error={typeof sipAmount === "number" && sipAmount > income ? "Cannot exceed your monthly income." : undefined}
                  >
                    <input type="number" inputMode="numeric" value={sipAmount} onChange={(e) => setSipAmount(e.target.value === "" ? "" : Number(e.target.value))} className="field w-full font-mono" placeholder="e.g. 25000" />
                  </Field>
                  <Field label="Annual step-up (%)">
                    <input type="number" inputMode="numeric" min={0} max={50} value={sipStepUp} onChange={(e) => setSipStepUp(e.target.value === "" ? "" : Number(e.target.value))} className="field w-full font-mono" placeholder="e.g. 10" />
                  </Field>
                </div>
                <p className="border-l-2 border-accent bg-bg-subtle p-4 text-step--1 leading-relaxed text-text-secondary">
                  Your funds&apos; past returns are not extrapolated. Tested on thirteen years of real NAVs, a fund&apos;s
                  history predicted its next three years poorly, so the expected return is shrunk toward a long-run prior —
                  and both numbers are shown on your results.
                </p>
              </>
            )}
          </div>

          {error && (
            <p role="alert" className="mt-4 border-t border-line pt-4 font-mono text-step--1 text-accent-text">
              {error}
            </p>
          )}

          <div className="mt-8 flex items-center justify-between border-t border-line pt-6">
            <button type="button" onClick={handleBack} disabled={step === 0} className="btn">
              <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Back
            </button>
            <button type="button" onClick={handleNext} disabled={isSaving} className="btn btn-primary">
              {isSaving ? "Saving…" : step === 4 ? "Save profile" : "Continue"}
              {!isSaving && (step === 4 ? <Check className="h-4 w-4" aria-hidden="true" /> : <ArrowRight className="h-4 w-4" aria-hidden="true" />)}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

function StepTitle({ n, title, sub }: { n: number; title: string; sub: string }) {
  return (
    <div className="border-b border-line pb-5">
      <p className="label text-accent-text">Step {n} of 5</p>
      <h2 className="mt-2 font-display text-step-3 font-semibold text-text">{title}</h2>
      <p className="mt-1 text-step--1 text-text-secondary">{sub}</p>
    </div>
  );
}

function Field({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-2">
      <span className="label">{label}</span>
      {children}
      {error && <span className="block font-mono text-step--2 text-accent-text">{error}</span>}
    </label>
  );
}

function Choice<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { label: string; value: T }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="label mb-2">{label}</legend>
      <div className="grid grid-cols-2 gap-2">
        {options.map((o) => {
          const on = value === o.value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(o.value)}
              className={`flex min-h-[48px] items-center justify-between border px-4 text-left text-step--1 font-medium transition-colors ${
                on ? "border-[var(--mark)] bg-[var(--mark)] text-[var(--mark-fg)]" : "border-line bg-bg text-text hover:border-line-strong"
              }`}
            >
              {o.label}
              {on && <Check className="h-4 w-4 text-[var(--mark-dot)]" aria-hidden="true" />}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
