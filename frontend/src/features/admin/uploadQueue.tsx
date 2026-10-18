import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { UploadJob } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useToast } from "../../components/Toast";
import { env } from "../../config/env";
import { uploadFailureMessage } from "./messages";

export type UploadPhase = "uploading" | "waiting" | "processing" | "ready" | "failed";

export type UploadItem = {
  id: string;
  batchId: number;
  name: string;
  size: number;
  phase: UploadPhase;
  bytesSent: number;
  pagesDone: number;
  pagesTotal: number;
  jobId: string | null;
  error: string | null;
};

type Pending = { item: UploadItem; file: File; groups: string[]; users: string[] };

type QueueValue = {
  items: UploadItem[];
  enqueue: (files: File[], groups: string[], users: string[]) => void;
  dismiss: () => void;
};

const QueueContext = createContext<QueueValue | null>(null);
const CONCURRENT_UPLOADS = 2;
const POLL_MS = 2000;
let seq = 0;
let batchSeq = 0;

const active = (p: UploadPhase) => p === "uploading" || p === "waiting" || p === "processing";

/** Background upload queue (UI-F17): XHR uploads two at a time (fetch cannot report upload
 *  progress), then job polling every 2 seconds. Lives above the routes, so closing the dialog
 *  does not stop anything; after a reload, job status comes from the backend (files table). */
export function UploadQueueProvider({ children }: { children: ReactNode }) {
  const { api, getToken } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [items, setItems] = useState<UploadItem[]>([]);
  const pending = useRef<Pending[]>([]);
  const running = useRef(0);
  const announced = useRef(new Set<number>());

  const patch = useCallback((id: string, change: Partial<UploadItem>) => {
    setItems((all) => all.map((i) => (i.id === id ? { ...i, ...change } : i)));
  }, []);

  const send = useCallback(
    (p: Pending, token: string): Promise<{ status: number; body: Record<string, string> | null }> =>
      new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${env.apiBase}/admin/uploads`);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (e) => patch(p.item.id, { bytesSent: e.loaded });
        xhr.onload = () => {
          let body: Record<string, string> | null = null;
          try {
            body = JSON.parse(xhr.responseText) as Record<string, string>;
          } catch {
            body = null;
          }
          resolve({ status: xhr.status, body });
        };
        xhr.onerror = () => resolve({ status: 0, body: null });
        const form = new FormData();
        form.append("file", p.file, p.file.name);
        p.groups.forEach((g) => form.append("groups[]", g));
        p.users.forEach((u) => form.append("users[]", u));
        xhr.send(form);
      }),
    [patch],
  );

  const pump = useCallback(() => {
    while (running.current < CONCURRENT_UPLOADS && pending.current.length > 0) {
      const p = pending.current.shift()!;
      running.current += 1;
      void (async () => {
        try {
          let res = await send(p, await getToken(30));
          if (res.status === 401) res = await send(p, await getToken(-1)); // retry once
          if (res.status === 202 && res.body?.job_id) {
            patch(p.item.id, { phase: "waiting", jobId: res.body.job_id, bytesSent: p.item.size });
            void queryClient.invalidateQueries({ queryKey: ["admin", "documents"] });
          } else {
            const code =
              res.status === 413
                ? "file_too_large"
                : res.status === 429
                  ? "rate_limited"
                  : (res.body?.error ?? null);
            patch(p.item.id, {
              phase: "failed",
              error: uploadFailureMessage(code, { existing_filename: res.body?.existing_filename }),
            });
          }
        } catch {
          patch(p.item.id, { phase: "failed", error: uploadFailureMessage("internal_error") });
        } finally {
          running.current -= 1;
          pump();
        }
      })();
    }
  }, [getToken, patch, queryClient, send]);

  // Poll jobs that are waiting or processing.
  useEffect(() => {
    const watching = items.filter(
      (i) => i.jobId && (i.phase === "waiting" || i.phase === "processing"),
    );
    if (watching.length === 0) return;
    const t = window.setTimeout(async () => {
      for (const item of watching) {
        try {
          const job = await api.request<UploadJob>(`/admin/uploads/${item.jobId}`);
          if (job.status === "indexed")
            patch(item.id, {
              phase: "ready",
              pagesDone: job.pages_total,
              pagesTotal: job.pages_total,
            });
          else if (job.status === "failed")
            patch(item.id, { phase: "failed", error: uploadFailureMessage(job.failure_reason) });
          else
            patch(item.id, {
              phase: job.status === "processing" ? "processing" : "waiting",
              pagesDone: job.pages_done,
              pagesTotal: job.pages_total,
            });
        } catch {
          /* keep polling */
        }
      }
      void queryClient.invalidateQueries({ queryKey: ["admin", "documents"] });
    }, POLL_MS);
    return () => window.clearTimeout(t);
  }, [api, items, patch, queryClient]);

  // Toast once per finished batch (UI-F17).
  useEffect(() => {
    const batches = new Map<number, UploadItem[]>();
    items.forEach((i) => batches.set(i.batchId, [...(batches.get(i.batchId) ?? []), i]));
    batches.forEach((list, batchId) => {
      if (announced.current.has(batchId) || list.some((i) => active(i.phase))) return;
      announced.current.add(batchId);
      const ready = list.filter((i) => i.phase === "ready").length;
      const failed = list.length - ready;
      const readyText = `${ready} ${ready === 1 ? "file is" : "files are"} ready`;
      toast.show(
        failed === 0
          ? readyText
          : `${readyText}, ${failed} ${failed === 1 ? "needs" : "need"} attention`,
      );
    });
  }, [items, toast]);

  const value = useMemo<QueueValue>(
    () => ({
      items,
      enqueue: (files, groups, users) => {
        const batchId = ++batchSeq;
        const created = files.map<Pending>((file) => ({
          file,
          groups,
          users,
          item: {
            id: `up${++seq}`,
            batchId,
            name: file.name,
            size: file.size,
            phase: "uploading",
            bytesSent: 0,
            pagesDone: 0,
            pagesTotal: 0,
            jobId: null,
            error: null,
          },
        }));
        setItems((all) => [...all, ...created.map((c) => c.item)]);
        pending.current.push(...created);
        pump();
      },
      dismiss: () => setItems((all) => all.filter((i) => active(i.phase))),
    }),
    [items, pump],
  );
  return <QueueContext.Provider value={value}>{children}</QueueContext.Provider>;
}

export function useUploadQueue(): QueueValue {
  const value = useContext(QueueContext);
  if (!value) throw new Error("useUploadQueue must be used inside an UploadQueueProvider");
  return value;
}

export function phaseLabel(i: UploadItem): string {
  switch (i.phase) {
    case "uploading":
      return "Uploading";
    case "waiting":
      return "Waiting";
    case "processing":
      return i.pagesTotal ? `Processing, page ${i.pagesDone} of ${i.pagesTotal}` : "Processing";
    case "ready":
      return "Ready";
    case "failed":
      return i.error ?? "Processing failed. Try again.";
  }
}

export function phaseProgress(i: UploadItem): number {
  if (i.phase === "uploading") return i.size ? Math.min(1, i.bytesSent / i.size) : 0;
  if (i.phase === "processing") return i.pagesTotal ? i.pagesDone / i.pagesTotal : 0;
  return i.phase === "waiting" ? 0 : 1;
}
