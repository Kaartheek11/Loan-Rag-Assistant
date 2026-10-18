import * as Dialog from "@radix-ui/react-dialog";
import { FileText, Upload, X } from "lucide-react";
import { useRef, useState } from "react";

import type { AdminUser } from "../../api/types";
import { Button } from "../../components/Button";
import { AccessPickers } from "./AccessPickers";
import { formatBytes, MAX_FILES, quickCheck } from "./messages";
import { useUploadQueue } from "./uploadQueue";

type Picked = { file: File; problem: string | null };

/** UI-F16: drop zone, file list, required access, "Upload N files". */
export function UploadDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const queue = useUploadQueue();
  const [picked, setPicked] = useState<Picked[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);

  const add = (files: FileList | File[]) => {
    setPicked((current) => {
      const next = [...current];
      for (const file of Array.from(files)) {
        const valid = next.filter((p) => !p.problem).length;
        const problem =
          quickCheck(file) ?? (valid >= MAX_FILES ? `Up to ${MAX_FILES} files per batch.` : null);
        next.push({ file, problem });
      }
      return next;
    });
  };
  const valid = picked.filter((p) => !p.problem);
  const hasAccess = groups.length > 0 || users.length > 0;

  const reset = () => {
    setPicked([]);
    setGroups([]);
    setUsers([]);
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) reset();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[92vh] w-[min(94vw,640px)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-2xl bg-surface text-ink shadow-xl">
          <header className="flex items-start justify-between border-b border-line px-6 py-4">
            <div>
              <Dialog.Title className="font-display text-panel font-semibold">
                Upload loan files
              </Dialog.Title>
              <Dialog.Description className="text-meta text-ink-muted">
                PDF files up to 50 MB each.
              </Dialog.Description>
            </div>
            <Dialog.Close
              aria-label="Close"
              className="rounded-lg p-1.5 text-ink-muted hover:bg-ink/5"
            >
              <X size={18} aria-hidden="true" />
            </Dialog.Close>
          </header>
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setOver(false);
                add(e.dataTransfer.files);
              }}
              data-testid="drop-zone"
              className={`flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition-all ${over ? "-translate-y-0.5 border-marigold-line bg-marigold/10 shadow-md" : "border-line"}`}
            >
              <Upload size={24} aria-hidden="true" className="text-spruce" />
              <p className="text-ui text-ink">
                Drop loan PDFs here, or{" "}
                <button
                  type="button"
                  onClick={() => input.current?.click()}
                  className="font-semibold text-spruce underline"
                >
                  browse your computer
                </button>
                .
              </p>
              <input
                ref={input}
                type="file"
                accept="application/pdf,.pdf"
                multiple
                className="sr-only"
                aria-label="Choose PDF files"
                onChange={(e) => {
                  if (e.target.files) add(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {picked.length > 0 && (
              <ul className="space-y-1.5" aria-label="Selected files">
                {picked.map((p, i) => (
                  <li
                    key={`${p.file.name}-${i}`}
                    className="flex items-center gap-2 rounded-lg bg-paper px-3 py-2"
                  >
                    <FileText size={16} aria-hidden="true" className="shrink-0 text-ink-muted" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-ui">{p.file.name}</span>
                      <span className={`text-meta ${p.problem ? "text-coral" : "text-ink-muted"}`}>
                        {p.problem ?? formatBytes(p.file.size)}
                      </span>
                    </span>
                    <button
                      type="button"
                      aria-label={`Remove ${p.file.name}`}
                      onClick={() => setPicked((all) => all.filter((_, j) => j !== i))}
                      className="rounded p-1 hover:bg-ink/10"
                    >
                      <X size={14} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <section aria-labelledby="who-can-read">
              <h3 id="who-can-read" className="font-display text-ui font-semibold">
                Who can read these files
              </h3>
              <div className="mt-3">
                <AccessPickers
                  groups={groups}
                  users={users}
                  onGroups={setGroups}
                  onUsers={setUsers}
                />
              </div>
              <p className="mt-3 text-meta text-ink-muted">
                Only people in these groups can find these files in answers. That includes you:
                being an admin doesn&apos;t let you read files.
              </p>
            </section>
          </div>
          <footer className="flex justify-end gap-2 border-t border-line px-6 py-4">
            <Dialog.Close asChild>
              <Button variant="secondary">Cancel</Button>
            </Dialog.Close>
            <Button
              disabled={valid.length === 0 || !hasAccess}
              onClick={() => {
                queue.enqueue(
                  valid.map((p) => p.file),
                  groups,
                  users.map((u) => u.user_id),
                );
                onOpenChange(false);
                reset();
              }}
            >
              <Upload size={16} aria-hidden="true" />
              Upload {valid.length} {valid.length === 1 ? "file" : "files"}
            </Button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
