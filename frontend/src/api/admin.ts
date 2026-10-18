import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "../auth/AuthContext";
import type { ApiError } from "./client";
import type { AdminDocumentsPage, AdminUser } from "./types";

export type StatusFilter = "all" | "indexed" | "pending" | "failed";

export function useAdminDocuments(q: string, status: StatusFilter) {
  const { api } = useAuth();
  return useInfiniteQuery<AdminDocumentsPage, ApiError | Error>({
    queryKey: ["admin", "documents", q, status],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "50" });
      if (q.trim()) params.set("q", q.trim());
      if (status !== "all") params.set("status", status);
      if (pageParam) params.set("cursor", String(pageParam));
      return api.request<AdminDocumentsPage>(`/admin/documents?${params}`);
    },
    getNextPageParam: (last) => last.next_cursor,
    // Keep processing rows live while anything is being processed (UI-F17).
    refetchInterval: (query) =>
      query.state.data?.pages.some((p) => p.summary.processing > 0) ? 2000 : false,
    retry: false,
  });
}

export function useGroups() {
  const { api } = useAuth();
  return useQuery<string[]>({
    queryKey: ["admin", "groups"],
    queryFn: () => api.request("/admin/groups"),
  });
}

export function useUserSearch(q: string) {
  const { api } = useAuth();
  return useQuery<AdminUser[]>({
    queryKey: ["admin", "users", q],
    queryFn: () => api.request(`/admin/users?q=${encodeURIComponent(q)}`),
    enabled: q.trim().length > 0,
  });
}

function useInvalidateDocuments() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["admin", "documents"] });
    void qc.invalidateQueries({ queryKey: ["me"] });
  };
}

export function useUpdateAccess() {
  const { api } = useAuth();
  const invalidate = useInvalidateDocuments();
  return useMutation({
    mutationFn: ({ docId, groups, users }: { docId: string; groups: string[]; users: string[] }) =>
      api.request(`/admin/documents/${encodeURIComponent(docId)}/acl`, {
        method: "PUT",
        body: JSON.stringify({ groups, users }),
      }),
    onSuccess: invalidate,
  });
}

export function useDeleteDocument() {
  const { api } = useAuth();
  const invalidate = useInvalidateDocuments();
  return useMutation({
    mutationFn: (docId: string) =>
      api.request(`/admin/documents/${encodeURIComponent(docId)}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

export function useRetryUpload() {
  const { api } = useAuth();
  const invalidate = useInvalidateDocuments();
  return useMutation({
    mutationFn: (jobId: string) =>
      api.request(`/admin/uploads/${encodeURIComponent(jobId)}/retry`, { method: "POST" }),
    onSuccess: invalidate,
  });
}
