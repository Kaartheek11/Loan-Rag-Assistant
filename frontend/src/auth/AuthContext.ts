import { createContext, useContext } from "react";

import type { ApiClient } from "../api/client";

export type AuthStatus = "loading" | "authenticated" | "anonymous" | "error";

export type AuthValue = {
  status: AuthStatus;
  api: ApiClient;
  /** Getter for upload XHRs, which cannot go through fetch (UI-F17). */
  getToken: (minValidity: number) => Promise<string>;
  signIn: () => void;
  signOut: () => void;
};

export const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside an AuthProvider");
  return value;
}
