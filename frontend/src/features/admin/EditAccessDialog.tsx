import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useState } from "react";

import { useUpdateAccess } from "../../api/admin";
import type { AdminDocument, AdminUser } from "../../api/types";
import { Button } from "../../components/Button";
import { useToast } from "../../components/Toast";
import { AccessPickers } from "./AccessPickers";
import { uploadFailureMessage } from "./messages";

/** UI-F19: the same pickers, prefilled; at least one group or person. */
export function EditAccessDialog({ doc, onClose }: { doc: AdminDocument; onClose: () => void }) {
  const [groups, setGroups] = useState<string[]>(doc.groups);
  const [users, setUsers] = useState<AdminUser[]>(
    doc.users.map((u) => ({ user_id: u, display: u })),
  );
  const update = useUpdateAccess();
  const toast = useToast();
  const hasAccess = groups.length > 0 || users.length > 0;
  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(94vw,560px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface p-6 text-ink shadow-xl">
          <div className="flex items-start justify-between">
            <Dialog.Title className="font-display text-panel font-semibold">
              Edit access
            </Dialog.Title>
            <Dialog.Close
              aria-label="Close"
              className="rounded-lg p-1.5 text-ink-muted hover:bg-ink/5"
            >
              <X size={18} aria-hidden="true" />
            </Dialog.Close>
          </div>
          <Dialog.Description className="mb-4 text-meta text-ink-muted">
            {doc.filename}
          </Dialog.Description>
          <AccessPickers groups={groups} users={users} onGroups={setGroups} onUsers={setUsers} />
          {!hasAccess && (
            <p className="mt-3 text-meta text-coral">{uploadFailureMessage("invalid_access")}</p>
          )}
          {update.isError && (
            <p className="mt-3 text-meta text-coral">{uploadFailureMessage("invalid_access")}</p>
          )}
          <div className="mt-6 flex justify-end gap-2">
            <Dialog.Close asChild>
              <Button variant="secondary">Cancel</Button>
            </Dialog.Close>
            <Button
              disabled={!hasAccess || update.isPending}
              onClick={() =>
                update.mutate(
                  { docId: doc.doc_id, groups, users: users.map((u) => u.user_id) },
                  {
                    onSuccess: () => {
                      toast.show("Access updated");
                      onClose();
                    },
                  },
                )
              }
            >
              Save access
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
