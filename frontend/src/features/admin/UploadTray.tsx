import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

import { phaseLabel, phaseProgress, useUploadQueue } from "./uploadQueue";

/** UI-F17: background progress in a corner tray; collapses to a pill; gone when all done. */
export function UploadTray() {
  const { items } = useUploadQueue();
  const [open, setOpen] = useState(true);
  const working = items.filter(
    (i) => i.phase === "uploading" || i.phase === "waiting" || i.phase === "processing",
  );
  if (working.length === 0) return null;
  const batch = items.filter((i) => i.batchId >= Math.min(...working.map((w) => w.batchId)));
  return (
    <aside
      aria-label="Uploads"
      className="fixed bottom-4 right-4 z-40 w-[min(92vw,360px)] rounded-2xl border border-line bg-surface text-ink shadow-xl"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-ui font-semibold"
      >
        <span>Uploads · {working.length} processing</span>
        {open ? (
          <ChevronDown size={16} aria-hidden="true" />
        ) : (
          <ChevronUp size={16} aria-hidden="true" />
        )}
      </button>
      {open && (
        <ul className="max-h-72 space-y-3 overflow-y-auto border-t border-line px-4 py-3">
          {batch.map((i) => (
            <li key={i.id}>
              <p className="truncate text-ui">{i.name}</p>
              <p
                className={`text-meta ${i.phase === "failed" ? "text-coral" : "text-ink-muted"}`}
                data-testid="upload-status"
              >
                {phaseLabel(i)}
              </p>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-paper">
                <div
                  className={`h-full rounded-full ${i.phase === "failed" ? "bg-coral" : "bg-spruce"}`}
                  style={{ width: `${Math.round(phaseProgress(i) * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
