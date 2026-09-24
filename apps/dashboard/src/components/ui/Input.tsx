import { cn } from "@/lib/utils";
import type { InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

export function Input({ className, label, error, id, ...props }: InputProps) {
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, "-");
  return (
    <label className="flex flex-col gap-1 text-sm">
      {label && <span className="text-muted">{label}</span>}
      <input id={inputId} className={cn("w-full", className)} {...props} />
      {error && <span className="text-danger text-xs">{error}</span>}
    </label>
  );
}
