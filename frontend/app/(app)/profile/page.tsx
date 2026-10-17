"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { createProfile, getProfile, ApiRequestError } from "@/lib/api-client";
import { PageHeader, DemoBadge } from "@/components/shared";
import { motion, AnimatePresence } from "motion/react";
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
    return <p className="text-ink-soft text-sm font-mono">Loading…</p>;
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

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      {/* Page Header */}
      <PageHeader
        eyebrow="Phase 1 · Onboarding"
        title="Investor Profile Wizard"
        description="Configure your financial coordinates. These metrics form the bounds for our mathematical simulations."
        action={<DemoBadge />}
      />

      {/* Step Progress Bar */}
      <div className="space-y-3 select-none">
        <div className="flex gap-1.5">
          {stepsList.map((_, idx) => (
            <div
              key={idx}
              className={`h-1.5 flex-1 transition-all duration-350 ${
                idx <= step ? "bg-crimson" : "bg-line"
              }`}
            />
          ))}
        </div>
        <div className="flex justify-between text-[10px] font-mono uppercase tracking-wider text-ink-soft">
          {stepsList.map((label, idx) => (
            <span
              key={idx}
              className={idx === step ? "text-crimson font-bold" : "text-ink-soft/60"}
            >
              {idx + 1}. {label}
            </span>
          ))}
        </div>
      </div>

      {/* Active Form Step Cards */}
      <div className="border border-line p-6 md:p-8 bg-paper relative overflow-hidden min-h-[380px] flex flex-col justify-between">
        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 15 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -15 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="space-y-6 flex-1"
          >
            {/* Step 0: About You */}
            {step === 0 && (
              <div className="space-y-6">
                <div className="border-b border-line pb-4 mb-4">
                  <h2 className="font-display text-xl font-semibold text-ink">Personal Coordinates</h2>
                  <p className="text-xs text-ink-soft">We gather basic metadata for customized interface logs.</p>
                </div>

                <div className="grid md:grid-cols-2 gap-6">
                  <label className="block">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Full Name</span>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors"
                      placeholder="e.g. Rajesh Kumar"
                    />
                  </label>

                  <label className="block">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Date of Birth</span>
                    <input
                      type="date"
                      value={dob}
                      onChange={(e) => setDob(e.target.value)}
                      className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors"
                    />
                  </label>
                </div>

                <label className="block">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Occupation</span>
                  <input
                    type="text"
                    value={occupation}
                    onChange={(e) => setOccupation(e.target.value)}
                    className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors"
                    placeholder="e.g. Salaried Corporate, Business Proprietor"
                  />
                </label>
              </div>
            )}

            {/* Step 1: Finances */}
            {step === 1 && (
              <div className="space-y-6">
                <div className="border-b border-line pb-4 mb-4">
                  <h2 className="font-display text-xl font-semibold text-ink">Financial Balance Sheet</h2>
                  <p className="text-xs text-ink-soft">State your monthly flows and existing assets in Indian Rupees (₹).</p>
                </div>

                <div className="grid md:grid-cols-2 gap-6">
                  <label className="block">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Net Monthly Income (₹)</span>
                    <input
                      type="number"
                      value={income}
                      onChange={(e) => setIncome(Number(e.target.value))}
                      className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors font-mono"
                    />
                  </label>

                  <label className="block">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Total Corpus (₹)</span>
                    <input
                      type="number"
                      value={corpus}
                      onChange={(e) => setCorpus(Number(e.target.value))}
                      className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors font-mono"
                    />
                  </label>
                </div>

                <label className="block">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">
                    Amount to Invest Now (₹){" "}
                    {investNow > corpus && (
                      <span className="text-crimson normal-case font-sans font-normal">— cannot exceed your total corpus</span>
                    )}
                  </span>
                  <input
                    type="number"
                    value={investNow}
                    onChange={(e) => setInvestNow(Number(e.target.value))}
                    className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors font-mono"
                  />
                </label>
              </div>
            )}

            {/* Step 2: Goal and Risk */}
            {step === 2 && (
              <div className="space-y-6">
                <div className="border-b border-line pb-4 mb-4">
                  <h2 className="font-display text-xl font-semibold text-ink">Mandate & Risk Appetite</h2>
                  <p className="text-xs text-ink-soft">Risk matches do not hard-block allocations; they surface as a specific warning on schemes above your comfort level.</p>
                </div>

                <div className="space-y-2">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-ink font-mono">Investment Goal</span>
                  <div className="grid grid-cols-2 gap-2">
                    {GOAL_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setGoal(opt.value)}
                        className={`px-4 py-3 text-xs font-semibold tracking-wider font-mono uppercase text-left border flex justify-between items-center transition-colors cursor-pointer ${
                          goal === opt.value ? "bg-ink text-paper border-ink" : "bg-paper text-ink border-line hover:border-ink"
                        }`}
                      >
                        {opt.label}
                        {goal === opt.value && <Check className="w-4 h-4" />}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-ink font-mono">Risk Appetite</span>
                  <div className="grid grid-cols-2 gap-2">
                    {RISK_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setRiskAppetite(opt.value)}
                        className={`px-4 py-3 text-xs font-semibold tracking-wider font-mono uppercase text-left border flex justify-between items-center transition-colors cursor-pointer ${
                          riskAppetite === opt.value ? "bg-ink text-paper border-ink" : "bg-paper text-ink border-line hover:border-ink"
                        }`}
                      >
                        {opt.label}
                        {riskAppetite === opt.value && <Check className="w-4 h-4" />}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Step 3: Retirement */}
            {step === 3 && (
              <div className="space-y-6">
                <div className="border-b border-line pb-4 mb-4">
                  <h2 className="font-display text-xl font-semibold text-ink">Retirement Milestones</h2>
                  <p className="text-xs text-ink-soft">Define the decumulation bounds for the Monte Carlo model.</p>
                </div>

                <div className="space-y-2">
                  <div className="flex justify-between items-center">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink font-mono">Target Retirement Age</span>
                    <span className="font-mono text-xs font-bold text-crimson bg-crimson/5 px-2 py-0.5 border border-crimson/25">{retirementAge} years old</span>
                  </div>
                  <input
                    type="range"
                    min="40"
                    max="80"
                    value={retirementAge}
                    onChange={(e) => setRetirementAge(Number(e.target.value))}
                    className="h-1 w-full cursor-pointer rounded-[2px] bg-line accent-[var(--accent)]"
                  />
                  <div className="flex justify-between text-[10px] font-mono text-text-secondary">
                    <span>40 yrs</span>
                    <span>60 yrs (Standard)</span>
                    <span>80 yrs</span>
                  </div>
                </div>

                <label className="block">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Monthly Expenses in Retirement, today&apos;s money (₹)</span>
                  <input
                    type="number"
                    value={expenses}
                    onChange={(e) => setExpenses(Number(e.target.value))}
                    className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors font-mono"
                  />
                </label>
              </div>
            )}

            {/* Step 4: SIP Details */}
            {step === 4 && (
              <div className="space-y-6">
                <div className="border-b border-line pb-4 mb-4">
                  <h2 className="font-display text-xl font-semibold text-ink">Monthly SIP (Optional)</h2>
                  <p className="text-xs text-ink-soft">If skipped, the ARISE engine estimates your SIP at 20% of income and labels it clearly as an estimate.</p>
                </div>

                <div className="grid md:grid-cols-2 gap-6">
                  <label className="block">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">
                      Monthly SIP (₹){" "}
                      {typeof sipAmount === "number" && sipAmount > income && (
                        <span className="text-crimson normal-case font-sans font-normal">— cannot exceed your monthly income</span>
                      )}
                    </span>
                    <input
                      type="number"
                      value={sipAmount}
                      onChange={(e) => setSipAmount(e.target.value === "" ? "" : Number(e.target.value))}
                      className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors font-mono"
                      placeholder="e.g. 25000"
                    />
                  </label>

                  <label className="block">
                    <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">Annual SIP Step-up (%)</span>
                    <input
                      type="number"
                      min={0}
                      max={50}
                      value={sipStepUp}
                      onChange={(e) => setSipStepUp(e.target.value === "" ? "" : Number(e.target.value))}
                      className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors font-mono"
                      placeholder="e.g. 10"
                    />
                  </label>
                </div>

                <div className="border border-line/50 bg-paper-tint/30 p-4 font-sans text-xs text-ink-soft leading-relaxed space-y-2">
                  <p className="font-semibold text-ink uppercase tracking-wide font-mono">How the expected return is calculated:</p>
                  <p>
                    ARISE does not extrapolate your funds&apos; recent historical average directly. The expected return is
                    shrunk toward a long-run prior, sample-weighted by how much real price history is actually available —
                    the raw historical average and the shrunk figure actually used are both shown side by side on the
                    results page, so you can see the difference for yourself.
                  </p>
                </div>
              </div>
            )}

          </motion.div>
        </AnimatePresence>

        {error && (
          <div className="mt-4 text-xs text-crimson font-mono flex items-center gap-1.5 border-t border-line pt-4">
            <span className="w-1 h-1 rounded-[1px] bg-crimson shrink-0" />
            {error}
          </div>
        )}

        {/* Action Controls */}
        <div className="mt-8 pt-6 border-t border-line flex justify-between items-center select-none">
          <button
            type="button"
            onClick={handleBack}
            className={`flex items-center gap-2 text-xs font-mono uppercase tracking-wider font-semibold py-2 px-3 transition-colors ${
              step === 0 ? "opacity-30 cursor-not-allowed text-ink-soft" : "text-ink hover:text-crimson"
            }`}
            disabled={step === 0}
          >
            <ArrowLeft className="w-3.5 h-3.5" /> Back
          </button>

          <button
            type="button"
            onClick={handleNext}
            disabled={isSaving}
            className="bg-ink text-paper px-6 py-2.5 text-xs font-mono uppercase tracking-widest font-semibold hover:bg-crimson transition-colors flex items-center gap-2 cursor-pointer disabled:opacity-50"
          >
            {isSaving ? "Saving…" : step === 4 ? "Complete Profile" : "Continue"}
            {!isSaving && (step === 4 ? <Check className="w-3.5 h-3.5" /> : <ArrowRight className="w-3.5 h-3.5" />)}
          </button>
        </div>

      </div>
    </div>
  );
}
