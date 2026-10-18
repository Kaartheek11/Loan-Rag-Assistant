import { useMutation } from "@tanstack/react-query";

import { useAuth } from "../auth/AuthContext";
import type { QueryResponse } from "./types";

/** POST /v1/query. Each question is answered on its own (no conversation memory). */
export function useAskQuestion() {
  const { api } = useAuth();
  return useMutation({
    mutationFn: (question: string) =>
      api.request<QueryResponse>("/query", { method: "POST", body: JSON.stringify({ question }) }),
    retry: false,
  });
}
