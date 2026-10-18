import type { ReactNode } from "react";

export function Chip({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-meta font-medium ${className}`}
    >
      {children}
    </span>
  );
}
