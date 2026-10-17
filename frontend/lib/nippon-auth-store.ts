"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  isAuthenticated: boolean;
  email: string | null;
  setAuthenticated: (email: string) => void;
  clear: () => void;
}

/**
 * Zustand + persist (localStorage) purely for UI state (is someone "logged
 * in" from this browser's perspective). The REAL access/refresh tokens live
 * in sessionStorage/localStorage via lib/api-client.ts's storeTokens --
 * this store never touches the token values themselves.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      isAuthenticated: false,
      email: null,
      setAuthenticated: (email) => set({ isAuthenticated: true, email }),
      clear: () => set({ isAuthenticated: false, email: null }),
    }),
    { name: "nippon-auth-ui" }
  )
);
