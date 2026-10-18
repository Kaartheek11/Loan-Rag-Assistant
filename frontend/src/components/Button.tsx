import { type ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "sidebar";

const styles: Record<Variant, string> = {
  primary: "bg-spruce text-on-spruce hover:bg-spruce/90 disabled:opacity-50",
  secondary: "bg-surface text-ink border border-line hover:bg-paper disabled:opacity-50",
  ghost: "text-ink hover:bg-ink/5 disabled:opacity-50",
  danger: "bg-coral text-on-coral hover:bg-coral/90 disabled:opacity-50",
  sidebar: "text-on-spruce-deep hover:bg-white/10 disabled:opacity-50",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }
>(function Button({ variant = "primary", className = "", type = "button", ...props }, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-ui font-medium transition-colors ${styles[variant]} ${className}`}
      {...props}
    />
  );
});
