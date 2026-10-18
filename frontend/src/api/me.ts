import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { useAuth } from "../auth/AuthContext";
import { ApiError, NotProvisionedError } from "./client";
import type { Me, MyDocumentsPage } from "./types";

const FIVE_MINUTES = 5 * 60 * 1000;

/** GET /v1/me: who the user is and what they can reach (UI-G2). Refetches on focus and
 *  every 5 minutes so the sidebar stays current (UI-G3). Display only (§2). */
export function useMe() {
  const { api } = useAuth();
  return useQuery<Me, ApiError | Error>({
    queryKey: ["me"],
    queryFn: () => api.request<Me>("/me", { on401: "no-access" }),
    staleTime: 0, // always refetch on focus (UI-G3)
    refetchOnWindowFocus: true,
    refetchInterval: FIVE_MINUTES,
    retry: (count, error) => !(error instanceof NotProvisionedError) && count < 1,
  });
}

/** GET /v1/me/documents, loaded page by page. */
export function useMyDocuments() {
  const { api } = useAuth();
  return useInfiniteQuery<MyDocumentsPage, Error>({
    queryKey: ["me", "documents"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api.request<MyDocumentsPage>(
        `/me/documents?limit=50${pageParam ? `&cursor=${encodeURIComponent(String(pageParam))}` : ""}`,
      ),
    getNextPageParam: (last) => last.next_cursor,
    staleTime: 0,
    refetchOnWindowFocus: true,
    refetchInterval: FIVE_MINUTES,
  });
}
