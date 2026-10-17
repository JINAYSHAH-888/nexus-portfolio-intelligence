"use client";

import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { ApiRequestError } from "@/lib/api-client";

/**
 * THE FIX for a real, confirmed bug: every analysis/scheme-detail/dashboard
 * page used `if (isLoading || !data) return <p>Loading...</p>` -- but once a
 * TanStack Query request FAILS, `isLoading` becomes false while `data` stays
 * undefined forever, so `isLoading || !data` stays true forever too. The
 * page hung on "Loading..." permanently instead of showing what actually
 * went wrong. Every caller of this component replaces that pattern.
 *
 * Maps the specific, real backend error shapes to actionable messages and
 * links, rather than a generic "something went wrong":
 *   - 409 profile_incomplete -> link to /profile
 *   - 404 (scheme selection not found) -> link to /schemes
 *   - 401 -> the session actually expired; AuthGuard handles the redirect,
 *     this is a fallback message in case that hasn't fired yet
 *   - anything else -> the real backend message + request_id, so a support
 *     conversation has something concrete to go on
 */
export function QueryError({ error }: { error: unknown }) {
  const isApiError = error instanceof ApiRequestError;
  const status = isApiError ? error.status : undefined;
  const message = isApiError ? error.body.message : "Something went wrong.";
  const requestId = isApiError ? error.body.request_id : undefined;

  if (status === 409) {
    return (
      <ErrorPanel title="Complete your profile first">
        <p>
          This needs your Deep Profile filled in before it can run. {message}
        </p>
        <Link href="/profile" className="inline-block mt-3 text-sm text-crimson border-b border-crimson">
          Go to your profile
        </Link>
      </ErrorPanel>
    );
  }

  if (status === 404) {
    // SelectionNotFoundError and a genuine "unknown scheme" 404 share the
    // same generic error code (both are subclasses of NotFoundError) --
    // the message text is the only thing that distinguishes them. The exact
    // string below is pinned server-side (CLAUDE.md §6), so matching on it
    // is safe, not fragile.
    const isMissingSelection = message.includes("Scheme selection not found");
    if (isMissingSelection) {
      return (
        <ErrorPanel title="Select your schemes first">
          <p>
            This needs at least one scheme selected before it can run. {message}
          </p>
          <Link href="/schemes" className="inline-block mt-3 text-sm text-crimson border-b border-crimson">
            Browse schemes
          </Link>
        </ErrorPanel>
      );
    }
    return (
      <ErrorPanel title="Not found">
        <p>{message}</p>
      </ErrorPanel>
    );
  }

  if (status === 401) {
    return (
      <ErrorPanel title="Your session expired">
        <p>Sign in again to continue.</p>
        <Link href="/login" className="inline-block mt-3 text-sm text-crimson border-b border-crimson">
          Sign in
        </Link>
      </ErrorPanel>
    );
  }

  if (status === 429) {
    return (
      <ErrorPanel title="Too many requests">
        <p>{message} Wait a moment and try again.</p>
      </ErrorPanel>
    );
  }

  return (
    <ErrorPanel title="Something went wrong">
      <p>{message}</p>
      {requestId && (
        <p className="mt-2 text-xs text-ink-soft font-mono">Reference: {requestId}</p>
      )}
    </ErrorPanel>
  );
}

function ErrorPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-crimson/30 bg-crimson/5 p-6 max-w-lg">
      <div className="flex items-center gap-2.5 mb-2">
        <AlertCircle size={18} className="text-crimson shrink-0" />
        <h2 className="font-display text-lg text-crimson">{title}</h2>
      </div>
      <div className="text-sm text-ink-soft leading-relaxed">{children}</div>
    </div>
  );
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-8">
      <span
        className="w-5 h-5 border-2 border-line border-t-crimson rounded-[1px] animate-spin shrink-0"
        aria-hidden="true"
      />
      <p className="text-ink-soft text-sm">{label}</p>
    </div>
  );
}
