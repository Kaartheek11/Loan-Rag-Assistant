import { createContext, type ReactNode, useContext, useMemo, useRef, useState } from "react";

export type SourceTarget = {
  docId: string;
  filename: string;
  page: number;
  /** Cited page and passage; the highlight only applies on this page (UI-F13). */
  citedPage: number | null;
  excerpt: string | null;
};

type SourceValue = {
  target: SourceTarget | null;
  open: (target: SourceTarget, opener: HTMLElement | null) => void;
  goTo: (page: number) => void;
  close: () => void;
  openerRef: React.MutableRefObject<HTMLElement | null>;
};

const SourceContext = createContext<SourceValue | null>(null);

export function SourceProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<SourceTarget | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const value = useMemo<SourceValue>(
    () => ({
      target,
      openerRef,
      open: (t, opener) => {
        openerRef.current = opener;
        setTarget(t);
      },
      goTo: (page) => setTarget((t) => (t ? { ...t, page } : t)),
      close: () => setTarget(null),
    }),
    [target],
  );
  return <SourceContext.Provider value={value}>{children}</SourceContext.Provider>;
}

export function useSource(): SourceValue {
  const value = useContext(SourceContext);
  if (!value) throw new Error("useSource must be used inside a SourceProvider");
  return value;
}
