"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { register as apiRegister, ApiRequestError } from "@/lib/api-client";
import { useAuthStore } from "@/lib/nippon-auth-store";

export default function RegisterPage() {
  const router = useRouter();
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!name) {
      setError("Full name is required.");
      return;
    }
    if (!email) {
      setError("Email address is required.");
      return;
    }
    if (!password) {
      setError("Password is required.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setIsLoading(true);
    try {
      await apiRegister(email, password, name);
      setAuthenticated(email);
      router.push("/profile"); // Direct to the 5-step profile wizard
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.body.message : "Could not create your account.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="w-full"
    >
      <div className="mb-8">
        <h1 className="font-display text-3xl font-semibold tracking-tight text-ink mb-1">
          Create your profile
        </h1>
        <p className="text-sm text-ink-soft">
          Ten fields. No credit card. Fully compliant analytics.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block">
            <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">
              Full Name
            </span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-soft/40 focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors"
              placeholder="e.g. Rajesh Kumar"
              disabled={isLoading}
            />
          </label>
        </div>

        <div>
          <label className="block">
            <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">
              Email Address
            </span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-soft/40 focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors"
              placeholder="e.g. rajesh@gmail.com"
              disabled={isLoading}
            />
          </label>
        </div>

        <div>
          <label className="block">
            <span className="block text-xs font-semibold uppercase tracking-wider text-ink mb-1.5 font-mono">
              Password
            </span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border border-line bg-paper px-3.5 py-2.5 text-sm text-ink placeholder:text-ink-soft/40 focus:outline-none focus:border-crimson focus:ring-1 focus:ring-crimson transition-colors"
              placeholder="••••••••"
              disabled={isLoading}
            />
            <span className="block mt-1.5 text-xs text-ink-soft leading-normal font-mono">
              At least 8 characters, with a number or a special character.
            </span>
          </label>
        </div>

        {error && (
          <div className="text-xs text-crimson font-mono mt-1 flex items-center gap-1">
            <span className="w-1 h-1 rounded-full bg-crimson" />
            {error}
          </div>
        )}

        <button
          type="submit"
          className="w-full bg-ink text-paper px-6 py-3 text-sm font-semibold uppercase tracking-wider hover:bg-crimson transition-colors font-mono cursor-pointer flex justify-center items-center h-11"
          disabled={isLoading}
        >
          {isLoading ? (
            <span className="w-4 h-4 border-2 border-paper border-t-transparent rounded-full animate-spin" />
          ) : (
            "Establish Secure Profile"
          )}
        </button>
      </form>

      <div className="mt-8 pt-6 border-t border-line text-center text-xs text-ink-soft">
        Already have an analytics profile?{" "}
        <Link href="/login" className="text-crimson hover:underline font-semibold font-mono">
          Sign In
        </Link>
      </div>
    </motion.div>
  );
}
