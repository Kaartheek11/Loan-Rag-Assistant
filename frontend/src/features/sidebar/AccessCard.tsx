import { useEffect, useRef, useState } from "react";

import type { Me } from "../../api/types";
import { useToast } from "../../components/Toast";
import { formatCount, friendlyLabel } from "../../lib/labels";

/** Groups and readable file count from /v1/me (display only, §2). When the groups change on
 *  a refetch, the card gets a brief highlight and a toast (UI-F4, UI-G3). */
export function AccessCard({ me }: { me: Me }) {
  const toast = useToast();
  const previous = useRef<string | null>(null);
  const [flash, setFlash] = useState(false);
  const key = [...me.groups].sort().join("|");

  useEffect(() => {
    if (previous.current !== null && previous.current !== key) {
      setFlash(true);
      toast.show("Your access was updated.");
      const t = window.setTimeout(() => setFlash(false), 2000);
      previous.current = key;
      return () => window.clearTimeout(t);
    }
    previous.current = key;
    return undefined;
  }, [key, toast]);

  return (
    <section
      aria-label="Your access"
      data-testid="access-card"
      data-updated={flash ? "true" : "false"}
      className={`rounded-xl px-3 py-3 transition-colors duration-500 ${flash ? "bg-marigold/25" : "bg-white/5"}`}
    >
      {me.groups.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {me.groups.map((g) => (
            <li
              key={g}
              className="rounded-full bg-white/10 px-2.5 py-0.5 text-meta text-on-spruce-deep"
            >
              {friendlyLabel(g)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-meta text-on-spruce-deep-muted">No access groups</p>
      )}
      <p className="mt-2 text-ui font-medium text-on-spruce-deep tabular">
        {formatCount(me.readable_document_count, "loan file", "loan files")}
      </p>
    </section>
  );
}
