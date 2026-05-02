"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Token, User } from "./types";

interface AuthState {
  user: User | null;
  token: Token | null;
  /**
   * True after zustand's persist middleware has finished pulling the
   * stored session out of localStorage. Components that gate rendering
   * on auth must wait for this; otherwise the first paint sees the
   * default `null` token and bounces the user to /login on every refresh.
   */
  hydrated: boolean;
  setSession: (user: User, token: Token) => void;
  setToken: (token: Token) => void;
  clear: () => void;
  _setHydrated: (value: boolean) => void;
}

/**
 * Tokens live in localStorage so the app survives reloads. The app is
 * rendered behind Board RBAC; XSS exposure is acceptable for an
 * internal tool, and access tokens are short-lived (default 8h, but the
 * refresh flow extends as long as the refresh token is alive).
 */
export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      hydrated: false,
      setSession: (user, token) => set({ user, token }),
      setToken: (token) => set({ token }),
      clear: () => set({ user: null, token: null }),
      _setHydrated: (value) => set({ hydrated: value }),
    }),
    {
      name: "acf.board.auth",
      // ``hydrated`` is a derived flag — never store it.
      partialize: (state) => ({ user: state.user, token: state.token }),
      // Fires after rehydration completes (whether or not a stored
      // session existed). Errors are surfaced as the second arg.
      onRehydrateStorage: () => (state, error) => {
        if (error) {
          // eslint-disable-next-line no-console
          console.warn("auth-store rehydration failed", error);
        }
        state?._setHydrated(true);
      },
    },
  ),
);
