import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { AnimatePresence, motion } from "framer-motion";
import { MoreHorizontal, Search, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  type StatusFilter,
  useAdminDocuments,
  useDeleteDocument,
  useRetryUpload,
} from "../../api/admin";
import { ApiError } from "../../api/client";
import type { AdminDocument } from "../../api/types";
import { Button } from "../../components/Button";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useToast } from "../../components/Toast";
import { formatCount, friendlyLabel } from "../../lib/labels";
import { EditAccessDialog } from "./EditAccessDialog";
import { uploadFailureMessage } from "./messages";
import { UploadDialog } from "./UploadDialog";

function StatusBadge({ doc }: { doc: AdminDocument }) {
  if (doc.status === "indexed")
    return (
      <span className="rounded-full bg-spruce/15 px-2.5 py-0.5 text-meta font-medium text-ink">
        Ready
      </span>
    );
  if (doc.status === "failed")
    return (
      <span className="inline-flex flex-col">
        <span className="w-fit rounded-full bg-coral/15 px-2.5 py-0.5 text-meta font-medium text-ink">
          Failed
        </span>
        <span className="mt-0.5 text-meta text-ink-muted">
          {uploadFailureMessage(doc.upload?.failure_reason ?? "internal_error")}
        </span>
      </span>
    );
  const up = doc.upload;
  const pct = up && up.pages_total ? Math.round((up.pages_done / up.pages_total) * 100) : 0;
  return (
    <span className="rounded-full bg-marigold/25 px-2.5 py-0.5 text-meta font-medium text-ink tabular">
      {up?.status === "processing"
        ? `Processing ${pct}%`
        : up?.status === "queued"
          ? "Waiting"
          : "Processing"}
    </span>
  );
}

function AccessCell({ doc }: { doc: AdminDocument }) {
  const labels = [...doc.groups.map(friendlyLabel), ...doc.users];
  if (labels.length === 0) return <span className="text-meta text-ink-muted">Nobody</span>;
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className="rounded-full bg-paper px-2 py-0.5 text-meta">{labels[0]}</span>
      {labels.length > 1 && <span className="text-meta text-ink-muted">+{labels.length - 1}</span>}
    </span>
  );
}

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "indexed", label: "Ready" },
  { value: "pending", label: "Processing" },
  { value: "failed", label: "Failed" },
];

/** UI-F15: metadata only. No "open" action: being an admin does not grant read access. */
export function FilesPage() {
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [uploading, setUploading] = useState(false);
  const [editing, setEditing] = useState<AdminDocument | null>(null);
  const [deleting, setDeleting] = useState<AdminDocument | null>(null);
  const docs = useAdminDocuments(search, status);
  const del = useDeleteDocument();
  const retry = useRetryUpload();
  const toast = useToast();
  const sentinel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setSearch(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && docs.hasNextPage && !docs.isFetchingNextPage)
        void docs.fetchNextPage();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [docs]);

  const rows = useMemo(() => docs.data?.pages.flatMap((p) => p.items) ?? [], [docs.data]);
  const summary = docs.data?.pages[0]?.summary;

  if (docs.error instanceof ApiError && docs.error.status === 403) {
    return (
      <section className="flex h-full items-center justify-center bg-surface p-8">
        <p className="text-message text-ink">This page is for administrators.</p>
      </section>
    );
  }

  return (
    <section aria-label="Loan files" className="flex h-full min-h-0 flex-col bg-surface">
      <header className="flex flex-wrap items-center justify-between gap-3 px-6 pb-3 pt-6">
        <h1 className="font-display text-greeting font-semibold text-ink">Loan files</h1>
        <Button onClick={() => setUploading(true)}>
          <Upload size={16} aria-hidden="true" />
          Upload files
        </Button>
      </header>
      {summary && (
        <div className="flex flex-wrap gap-2 px-6" aria-label="Summary">
          <button
            type="button"
            onClick={() => setStatus("all")}
            className="rounded-full border border-line px-3 py-1 text-ui tabular hover:bg-paper"
          >
            {formatCount(summary.total, "file", "files")}
          </button>
          <button
            type="button"
            onClick={() => setStatus("pending")}
            className="rounded-full border border-line px-3 py-1 text-ui tabular hover:bg-paper"
          >
            {summary.processing.toLocaleString("en-US")} processing
          </button>
          <button
            type="button"
            onClick={() => setStatus("failed")}
            className="rounded-full border border-line px-3 py-1 text-ui tabular hover:bg-paper"
          >
            {summary.failed.toLocaleString("en-US")} {summary.failed === 1 ? "needs" : "need"}{" "}
            attention
          </button>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 px-6 py-4">
        <label className="relative block min-w-56 flex-1">
          <span className="sr-only">Search by filename</span>
          <Search
            size={14}
            aria-hidden="true"
            className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted"
          />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by filename"
            className="w-full rounded-lg border border-line bg-surface py-2 pl-8 pr-3 text-ui"
          />
        </label>
        <label className="flex items-center gap-2 text-ui text-ink">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
            className="rounded-lg border border-line bg-surface px-2 py-2 text-ui"
          >
            {FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-6 pb-6">
        {docs.isSuccess && rows.length === 0 ? (
          <p className="py-10 text-center text-message text-ink-muted">
            No loan files yet. Upload PDFs to make them searchable.
          </p>
        ) : (
          <table className="w-full min-w-[760px] border-collapse text-left text-ui">
            <thead className="text-meta text-ink-muted">
              <tr className="border-b border-line">
                <th className="py-2 pr-3 font-medium">File</th>
                <th className="py-2 pr-3 font-medium">Pages</th>
                <th className="py-2 pr-3 font-medium">Access</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Uploaded</th>
                <th className="py-2 pr-3 font-medium">Uploaded by</th>
                <th className="py-2 font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false}>
                {rows.map((doc) => (
                  <motion.tr
                    key={doc.doc_id}
                    layout
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="border-b border-line align-top"
                    data-testid="file-row"
                  >
                    <td className="max-w-64 truncate py-2.5 pr-3 font-medium">{doc.filename}</td>
                    <td className="py-2.5 pr-3 tabular">
                      {doc.status === "indexed" || doc.page_count ? doc.page_count : "-"}
                    </td>
                    <td className="py-2.5 pr-3">
                      <AccessCell doc={doc} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusBadge doc={doc} />
                    </td>
                    <td className="py-2.5 pr-3 tabular text-ink-muted">
                      {new Date(doc.ingested_at).toLocaleDateString()}
                    </td>
                    <td className="py-2.5 pr-3 text-ink-muted">{doc.uploaded_by ?? "-"}</td>
                    <td className="py-2.5">
                      <DropdownMenu.Root>
                        <DropdownMenu.Trigger
                          aria-label={`Actions for ${doc.filename}`}
                          className="rounded-lg p-1.5 hover:bg-ink/5"
                        >
                          <MoreHorizontal size={16} aria-hidden="true" />
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Portal>
                          <DropdownMenu.Content
                            align="end"
                            className="z-50 min-w-40 rounded-xl border border-line bg-surface p-1 text-ink shadow-lg"
                          >
                            <DropdownMenu.Item
                              onSelect={() => setEditing(doc)}
                              className="cursor-pointer rounded-lg px-3 py-2 text-ui outline-none data-[highlighted]:bg-paper"
                            >
                              Edit access
                            </DropdownMenu.Item>
                            {doc.status === "failed" && doc.upload && (
                              <DropdownMenu.Item
                                onSelect={() => retry.mutate(doc.upload!.job_id)}
                                className="cursor-pointer rounded-lg px-3 py-2 text-ui outline-none data-[highlighted]:bg-paper"
                              >
                                Retry
                              </DropdownMenu.Item>
                            )}
                            <DropdownMenu.Item
                              onSelect={() => setDeleting(doc)}
                              className="cursor-pointer rounded-lg px-3 py-2 text-ui text-coral outline-none data-[highlighted]:bg-paper"
                            >
                              Delete
                            </DropdownMenu.Item>
                          </DropdownMenu.Content>
                        </DropdownMenu.Portal>
                      </DropdownMenu.Root>
                    </td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
        )}
        <div ref={sentinel} aria-hidden="true" className="h-4" />
      </div>
      <UploadDialog open={uploading} onOpenChange={setUploading} />
      {editing && <EditAccessDialog doc={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.filename ?? ""}?`}
        description="It will stop appearing in answers for everyone."
        confirmLabel="Delete file"
        danger
        onConfirm={() => {
          if (deleting)
            del.mutate(deleting.doc_id, { onSuccess: () => toast.show("File deleted") });
        }}
      />
    </section>
  );
}
