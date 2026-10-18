/** LoanLens mark: a page with a marigold highlighter stroke. */
export function LogoMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      className={className}
      focusable="false"
    >
      <rect x="6" y="3" width="20" height="26" rx="4" fill="rgb(var(--spruce))" />
      <rect x="10" y="9" width="12" height="2" rx="1" fill="rgb(var(--on-spruce))" opacity="0.8" />
      <rect x="9" y="14" width="15" height="5" rx="2.5" fill="rgb(var(--marigold))" />
      <rect x="10" y="22" width="9" height="2" rx="1" fill="rgb(var(--on-spruce))" opacity="0.8" />
    </svg>
  );
}
