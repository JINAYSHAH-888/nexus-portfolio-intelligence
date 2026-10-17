"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { onSessionExpired } from "@/lib/api-client";
import { useAuthStore } from "@/lib/nippon-auth-store";

const TOKEN_KEY = "nippon_access_token";

/**
 * THE FIX for a real, confirmed bug: `(app)` routes had zero auth guard and
 * no middleware.ts existed anywhere in the project. If a token was missing
 * or expired, every page under /dashboard, /schemes, /analysis/* just let
 * every query 401 out silently instead of sending the investor to /login.
 *
 * Why this is a CLIENT component, not middleware: tokens live in
 * sessionStorage/localStorage (a stated, documented tradeoff -- see
 * api-client.ts), not cookies. Next.js middleware runs server-side/at the
 * edge and has no access to browser storage at all, so it's structurally
 * incapable of checking this. A real fix here means either this client-side
 * guard, or migrating auth to httpOnly cookies (a real backend change, not
 * a frontend-only one).
 *
 * Uses useSyncExternalStore rather than useState+useEffect specifically
 * because sessionStorage is EXTERNAL mutable state React doesn't own --
 * that's exactly what this hook exists for, and it avoids both the
 * hydration mismatch (server has no sessionStorage) and the
 * setState-inside-an-effect cascading-render pattern a naive
 * useState/useEffect version would need.
 *
 * Two ways the "authenticated" snapshot can change after mount:
 *   1. The custom 'nippon:session-expired' event (a 401 mid-visit that a
 *      silent refresh couldn't recover) -- subscribed to directly.
 *   2. Another tab logging out / clearing storage -- covered by the native
 *      'storage' event, which sessionStorage changes don't fire for WITHIN
 *      the same tab, but DO fire cross-tab via localStorage-backed signals;
 *      subscribing to both costs nothing and closes that gap too.
 */
function subscribe(callback: () => void) {
  const offSessionExpired = onSessionExpired(callback);
  window.addEventListener("storage", callback);
  return () => {
    offSessionExpired();
    window.removeEventListener("storage", callback);
  };
}

function getSnapshot(): boolean {
  return !!sessionStorage.getItem(TOKEN_KEY);
}

function getServerSnapshot(): boolean {
  // No sessionStorage on the server -- must match what the client's FIRST
  // render (pre-hydration) will also see, or React flags a hydration
  // mismatch. "Not authenticated yet" is the only safe answer here.
  return false;
}

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const clearAuth = useAuthStore((s) => s.clear);
  const hasToken = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // THE FIX for a real, confirmed race condition: useSyncExternalStore's
  // getServerSnapshot() correctly returns false on the pre-hydration render
  // (there's no sessionStorage on the server, and returning anything else
  // would be a hydration mismatch) -- but that's a deliberately transient
  // value, corrected to the real client reading on the very next render.
  // Acting on it immediately (the previous version's bug) means a genuine
  // full page load of an authenticated route -- a browser refresh, a direct
  // URL, a bookmark -- redirects to /login even with a perfectly valid
  // token in sessionStorage, because the effect fires on that transient
  // false BEFORE the correction lands. `hydrated` gates the redirect
  // decision until after the client has actually mounted and re-evaluated
  // hasToken for real, closing that window entirely.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    queueMicrotask(() => setHydrated(true));
  }, []);

  useEffect(() => {
    if (hydrated && !hasToken) {
      clearAuth();
      router.replace("/login");
    }
  }, [hydrated, hasToken, clearAuth, router]);

  // Covers three cases with one guard: not yet hydrated (server-matching
  // render), hydrated but genuinely unauthenticated (redirect in flight),
  // and -- implicitly -- never flashes protected content in either case.
  if (!hydrated || !hasToken) return null;

  return <>{children}</>;
}
