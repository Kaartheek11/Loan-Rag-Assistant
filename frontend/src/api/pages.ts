import { useQuery } from "@tanstack/react-query";

import { useAuth } from "../auth/AuthContext";
import { ApiError } from "./client";
import type { PageResponse } from "./types";

/** GET /v1/documents/{id}/pages/{n}. The backend re-checks access on every call (E4). */
export function usePage(docId: string | null, page: number) {
  const { api } = useAuth();
  return useQuery<PageResponse, ApiError | Error>({
    queryKey: ["page", docId, page],
    queryFn: () =>
      api.request<PageResponse>(`/documents/${encodeURIComponent(docId!)}/pages/${page}`),
    enabled: Boolean(docId) && page >= 1,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
    staleTime: 0,
  });
}
