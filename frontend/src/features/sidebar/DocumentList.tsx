import { FileText, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useMyDocuments } from "../../api/me";
import { useSource } from "../source/SourceContext";

/** "Your files": loaded page by page as the user scrolls; search filters loaded items. */
export function DocumentList({ onOpen }: { onOpen?: () => void }) {
  const docs = useMyDocuments();
  const source = useSource();
  const [query, setQuery] = useState("");
  const sentinel = useRef<HTMLLIElement | null>(null);
  const items = useMemo(() => docs.data?.pages.flatMap((p) => p.items) ?? [], [docs.data]);
  const shown = query
    ? items.filter((d) => d.filename.toLowerCase().includes(query.trim().toLowerCase()))
    : items;

  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && docs.hasNextPage && !docs.isFetchingNextPage) {
        void docs.fetchNextPage();
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, [docs]);

  return (
    <section aria-label="Your files" className="flex min-h-0 flex-1 flex-col">
      <h2 className="px-1 text-meta font-semibold text-on-spruce-deep-muted">Your files</h2>
      <label className="relative mt-2 block">
        <span className="sr-only">Search your files</span>
        <Search
          size={14}
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-spruce-deep-muted"
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search files"
          className="w-full rounded-lg bg-white/10 py-2 pl-8 pr-3 text-ui text-on-spruce-deep placeholder:text-on-spruce-deep-muted"
        />
      </label>
      <ul className="mt-2 min-h-0 flex-1 overflow-y-auto pr-1" data-testid="file-list">
        {docs.isSuccess && items.length === 0 && (
          <li className="px-1 py-2 text-meta text-on-spruce-deep-muted">
            No loan files are shared with you yet.
          </li>
        )}
        {shown.map((d) => (
          <li key={d.doc_id}>
            <button
              type="button"
              onClick={(e) => {
                source.open(
                  {
                    docId: d.doc_id,
                    filename: d.filename,
                    page: 1,
                    citedPage: null,
                    excerpt: null,
                  },
                  e.currentTarget,
                );
                onOpen?.();
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-ui text-on-spruce-deep hover:bg-white/10"
            >
              <FileText size={14} aria-hidden="true" className="shrink-0 opacity-70" />
              <span className="truncate">{d.filename}</span>
            </button>
          </li>
        ))}
        <li ref={sentinel} aria-hidden="true" className="h-2" />
        {docs.isFetchingNextPage && (
          <li className="px-1 py-2 text-meta text-on-spruce-deep-muted">Loading more files</li>
        )}
      </ul>
    </section>
  );
}
