import { useEffect, useState } from "react";

export type Breakpoint = "phone" | "tablet" | "desktop";

function read(): Breakpoint {
  if (typeof window === "undefined" || !window.matchMedia) return "desktop";
  if (window.matchMedia("(min-width: 1024px)").matches) return "desktop";
  if (window.matchMedia("(min-width: 768px)").matches) return "tablet";
  return "phone";
}

/** Desktop >= 1024px, tablet 768-1023px, phone < 768px (extension.md §8.5). */
export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>(read);
  useEffect(() => {
    const update = () => setBp(read());
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return bp;
}
