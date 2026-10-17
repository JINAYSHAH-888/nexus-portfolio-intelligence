/**
 * API client for the real Nippon FastAPI backend.
 *
 * DEMO MODE: when NEXT_PUBLIC_API_BASE_URL is unset, every call resolves to
 * the realistic sample data in sample-data.ts instead of throwing or
 * rendering blank. This is a deliberate, VISIBLE choice -- isDemoMode() is
 * exported so every screen can render a "Sample data" badge. Nothing here
 * silently presents sample data as if it were live; see components/ui/demo-badge.tsx.
 *
 * The real backend's exact endpoint list, auth flow (JWT + rotating refresh),
 * and error shape are verified against backend/api/v1/*.py and backend/core/errors.py.
 */
import * as sample from "./sample-data";
import type {
  ProfileCreate,
  ProfileResponse,
  FilterResult,
  SchemeDetail,
  SelectedSchemes,
  SelectSchemesResult,
  OverlapResponse,
  PlosResponse,
  RiskResponse,
  AriseRequest,
  AriseResponse,
  CompareResponse,
  TokenPair,
  MeResponse,
  DashboardResponse,
  ApiError,
} from "./api-types";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");

export function isDemoMode(): boolean {
  return !BASE_URL;
}

const TOKEN_KEY = "nippon_access_token";
const REFRESH_KEY = "nippon_refresh_token";

/**
 * KNOWN SECURITY TRADEOFF, stated explicitly rather than left implicit.
 *
 * Tokens are stored in sessionStorage (access, short-lived, 30 min) and
 * localStorage (refresh, 14 days) -- both readable by any JavaScript running
 * on this origin, which is the standard XSS exposure of client-side token
 * storage. The backend's real defenses against a leaked token are what
 * actually matter here: refresh-token ROTATION with REUSE DETECTION (a
 * replayed refresh token revokes the entire session family server-side --
 * see backend/services/auth_service.py) and the 30-minute access-token
 * expiry, which bounds how long a stolen access token is useful even if the
 * refresh token isn't also compromised.
 *
 * httpOnly cookies would close the JS-readability gap entirely, but require
 * the backend to set/read cookies instead of returning tokens in the JSON
 * body -- a real architectural change to the auth flow, not a frontend-only
 * fix. Flagged here as the concrete next step if this ships to production
 * handling real money, not silently left as an unstated assumption.
 */
function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem(TOKEN_KEY);
}
function getStoredRefresh(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}
export function storeTokens(pair: TokenPair) {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(TOKEN_KEY, pair.access_token);
  localStorage.setItem(REFRESH_KEY, pair.refresh_token);
}
export function clearTokens() {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

/**
 * Fired whenever a session is known to be genuinely dead -- a 401 that a
 * silent refresh couldn't recover from (see the failed-refresh branch in
 * request() below), not just "no token was ever present." AuthGuard listens
 * for this to redirect mid-session, closing the gap where an access token
 * expiring 20 minutes into a visit would otherwise leave every subsequent
 * API call silently 401ing forever with no redirect and no explanation.
 */
const SESSION_EXPIRED_EVENT = "nippon:session-expired";
function announceSessionExpired() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}
export function onSessionExpired(handler: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(SESSION_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
}

class ApiRequestError extends Error {
  constructor(
    public status: number,
    public body: ApiError
  ) {
    super(body.message);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  retried = false,
  timeoutMs = 12_000
): Promise<T> {
  const token = getStoredToken();

  // TIMEOUT: Without this, a hanging backend, a CORS preflight that never
  // responds, or a network dropout means the promise NEVER rejects -- so
  // TanStack Query stays in `isLoading: true` forever, which is exactly the
  // "infinite loading" bug. AbortSignal.timeout() is built into every modern
  // browser; it throws AbortError after 15 seconds, which fetch() propagates
  // as a rejection, which TanStack Query sees as a failure and sets
  // `isError: true`, which finally shows the QueryError component.
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
  } catch (err) {
    clearTimeout(timeoutId);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiRequestError(0, {
        error: "timeout",
        message:
          "The request timed out. Check that the backend is running at " +
          BASE_URL +
          " and that CORS is configured to allow http://localhost:3000.",
      });
    }
    throw new ApiRequestError(0, {
      error: "network",
      message:
        "Could not reach the backend. Check that it is running at " +
        BASE_URL +
        ".",
    });
  } finally {
    clearTimeout(timeoutId);
  }

  if (res.status === 401 && !retried) {
    const refresh = getStoredRefresh();
    if (refresh) {
      try {
        const pair = await refreshTokens(refresh);
        storeTokens(pair);
        return request<T>(path, options, true, timeoutMs);
      } catch {
        clearTokens();
        announceSessionExpired();
      }
    } else {
      // No refresh token to even attempt recovery with -- equally dead.
      announceSessionExpired();
    }
  }

  if (!res.ok) {
    const body = (await res.json().catch(() => ({
      error: "unknown",
      message: res.statusText,
    }))) as ApiError;
    throw new ApiRequestError(res.status, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

async function refreshTokens(refresh_token: string): Promise<TokenPair> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10_000);
  try {
    const res = await fetch(`${BASE_URL}/auth/refresh`, {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token }),
    });
    if (!res.ok) throw new Error("refresh failed");
    return res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

// ── Auth ─────────────────────────────────────────────────────────────────
export async function register(
  email: string,
  password: string,
  full_name: string
) {
  if (isDemoMode()) {
    const pair: TokenPair = {
      access_token: "demo",
      refresh_token: "demo",
      token_type: "bearer",
    };
    storeTokens(pair);
    return pair;
  }
  const pair = await request<TokenPair>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name }),
  });
  storeTokens(pair);
  return pair;
}

export async function login(email: string, password: string) {
  if (isDemoMode()) {
    const pair: TokenPair = {
      access_token: "demo",
      refresh_token: "demo",
      token_type: "bearer",
    };
    storeTokens(pair);
    return pair;
  }
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: "unknown", message: "Login failed" }));
    throw new ApiRequestError(res.status, body);
  }
  const pair: TokenPair = await res.json();
  storeTokens(pair);
  return pair;
}

export async function me(): Promise<MeResponse> {
  if (isDemoMode())
    return { user_id: "demo", email: "demo@example.com", role: "investor", is_active: true };
  return request("/auth/me");
}

export async function logout() {
  const refresh = getStoredRefresh();
  clearTokens();
  if (isDemoMode() || !refresh) return;
  await fetch(`${BASE_URL}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  }).catch(() => {});
}

// ── Profile ──────────────────────────────────────────────────────────────
export async function createProfile(payload: ProfileCreate): Promise<ProfileResponse> {
  if (isDemoMode()) return { ...sample.SAMPLE_PROFILE, ...payload, id: "demo", user_id: "demo", is_complete: true, age: sample.SAMPLE_PROFILE.age, years_to_retirement: sample.SAMPLE_PROFILE.years_to_retirement };
  return request("/profile", { method: "POST", body: JSON.stringify(payload) });
}
export async function getProfile(): Promise<ProfileResponse> {
  if (isDemoMode()) return sample.SAMPLE_PROFILE;
  return request("/profile");
}

// ── Schemes ──────────────────────────────────────────────────────────────
export async function getFilteredSchemes(): Promise<FilterResult> {
  if (isDemoMode()) return sample.SAMPLE_FILTER_RESULT;
  return request("/schemes/filtered");
}
export async function getSchemeDetail(id: string): Promise<SchemeDetail> {
  if (isDemoMode())
    return (
      sample.SAMPLE_SCHEME_DETAIL[id] ??
      ({ ...sample.SAMPLE_SCHEMES[0], id } as SchemeDetail)
    );
  return request(`/schemes/${id}`);
}
export async function selectSchemes(
  scheme_ids: string[],
  weights?: Record<string, number>
): Promise<SelectSchemesResult> {
  if (isDemoMode())
    return {
      selected_count: scheme_ids.length,
      weights: weights ?? Object.fromEntries(scheme_ids.map((s) => [s, 1 / scheme_ids.length])),
      schemes: sample.SAMPLE_SCHEMES.filter((s) => scheme_ids.includes(s.id)),
      message: "Selection updated.",
    };
  return request("/schemes/select", {
    method: "POST",
    body: JSON.stringify({ selected_scheme_ids: scheme_ids, weights }),
  });
}
export async function allocateWeights(weights: Record<string, number>): Promise<SelectedSchemes> {
  if (isDemoMode()) throw new Error("allocate is a no-op in demo mode");
  return request("/schemes/allocate", { method: "POST", body: JSON.stringify({ weights }) });
}
export async function getSelectedSchemes(): Promise<SelectedSchemes> {
  if (isDemoMode())
    return {
      id: "demo",
      user_id: "demo",
      selected_scheme_ids: sample.SAMPLE_OVERLAP.scheme_ids,
      weights: sample.SAMPLE_ARISE.weights,
      schemes: sample.SAMPLE_SCHEMES.filter((s) =>
        sample.SAMPLE_OVERLAP.scheme_ids.includes(s.id)
      ),
    };
  return request("/schemes/selected");
}

// ── Analysis ─────────────────────────────────────────────────────────────
export async function getOverlap(): Promise<OverlapResponse> {
  if (isDemoMode()) return sample.SAMPLE_OVERLAP;
  return request("/analysis/overlap");
}
export async function getPlos(): Promise<PlosResponse> {
  if (isDemoMode()) return sample.SAMPLE_PLOS;
  return request("/analysis/plos");
}
export async function getRisk(): Promise<RiskResponse> {
  if (isDemoMode()) return sample.SAMPLE_RISK;
  return request("/analysis/risk");
}
export async function runArise(payload: AriseRequest = {}): Promise<AriseResponse> {
  if (isDemoMode())
    return new Promise((resolve) => setTimeout(() => resolve(sample.SAMPLE_ARISE), 1800));
  // ARISE runs up to 5,000 Monte Carlo simulations server-side -- 90 seconds
  // rather than the standard 12s, to avoid aborting a legitimate long-running
  // computation.
  return request("/analysis/arise", { method: "POST", body: JSON.stringify(payload) }, false, 90_000);
}
export async function compareSchemes(
  scheme_ids: string[],
  weights?: Record<string, number>
): Promise<CompareResponse> {
  if (isDemoMode())
    return {
      candidate: {
        scheme_ids,
        weights: weights ?? Object.fromEntries(scheme_ids.map((s) => [s, 1 / scheme_ids.length])),
        overlap: sample.SAMPLE_OVERLAP,
        plos: sample.SAMPLE_PLOS,
      },
      current: null,
    };
  return request("/analysis/compare", {
    method: "POST",
    body: JSON.stringify({ scheme_ids, weights }),
  });
}

// ── Dashboard ────────────────────────────────────────────────────────────
export async function getDashboard(): Promise<DashboardResponse> {
  if (isDemoMode())
    return {
      investor: {
        name: sample.SAMPLE_PROFILE.full_name,
        age: sample.SAMPLE_PROFILE.age,
        years_to_retirement: sample.SAMPLE_PROFILE.years_to_retirement,
        goal: sample.SAMPLE_PROFILE.goal,
        risk_appetite: sample.SAMPLE_PROFILE.risk_appetite,
      },
      portfolio: {
        scheme_count: 3,
        equity_schemes: 2,
        debt_schemes: 1,
        invested_amount: sample.SAMPLE_PROFILE.investment_amount,
        current_value: sample.SAMPLE_PROFILE.investment_amount,
        holdings: [],
      },
      market: { is_open: false, data_delayed: true, top_holdings: [] },
      pnl_available: false,
    };
  return request("/market/dashboard");
}

export { ApiRequestError };
