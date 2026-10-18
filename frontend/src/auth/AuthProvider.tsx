import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiClient } from "../api/client";
import { useToast } from "../components/Toast";
import { AuthContext, type AuthStatus, type AuthValue } from "./AuthContext";
import { initKeycloak, keycloak } from "./keycloak";

const SESSION_ENDED = "session=ended";

/** Signed-in state, silent token refresh, and sign out (extension.md §5.1, §10). */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const queryClient = useQueryClient();
  const toast = useToast();
  const endingRef = useRef(false);

  const sessionEnded = useCallback(() => {
    if (endingRef.current) return;
    endingRef.current = true;
    queryClient.clear();
    // After sign-in, the app shows "Your session ended" (a flag in the URL, never a token).
    void keycloak.login({ redirectUri: `${window.location.origin}/dashboard?${SESSION_ENDED}` });
  }, [queryClient]);

  const getToken = useCallback(async (minValidity: number) => {
    await keycloak.updateToken(minValidity);
    if (!keycloak.token) throw new Error("no token");
    return keycloak.token;
  }, []);

  const api = useMemo(
    () => new ApiClient({ getToken, onSessionEnded: sessionEnded }),
    [getToken, sessionEnded],
  );

  useEffect(() => {
    keycloak.onTokenExpired = () => {
      keycloak.updateToken(30).catch(() => sessionEnded());
    };
    initKeycloak()
      .then((authenticated) => setStatus(authenticated ? "authenticated" : "anonymous"))
      .catch(() => setStatus("error"));
    const params = new URLSearchParams(window.location.search);
    if (params.get("session") === "ended") {
      toast.show("Your session ended. Sign in again to continue.");
      params.delete("session");
      const rest = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : ""));
    }
  }, [sessionEnded, toast]);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      api,
      getToken,
      signIn: () => void keycloak.login({ redirectUri: `${window.location.origin}/dashboard` }),
      signOut: () => {
        // UI-F14: clear every cache and the chat, then end the Keycloak session.
        queryClient.clear();
        void keycloak.logout({ redirectUri: `${window.location.origin}/` });
      },
    }),
    [status, api, getToken, queryClient],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
