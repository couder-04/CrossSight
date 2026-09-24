import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
}

const variants: Record<Variant, string> = {
  primary: "bg-accent hover:bg-accent/90 text-white",
  secondary: "bg-surface-overlay hover:bg-surface-raised border border-border text-slate-100",
  ghost: "hover:bg-surface-overlay text-slate-200",
  danger: "bg-danger/90 hover:bg-danger text-white",
};

export function Button({
  className,
  variant = "primary",
  size = "md",
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none",
        size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm",
        variants[variant],
        className,
      )}
      disabled={disabled}
      {...props}
    />
  );
}
