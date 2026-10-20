import { forwardRef, type InputHTMLAttributes } from "react";
import { cx } from "./cx";

export const CONTROL =
  "w-full rounded-[var(--radius-sm)] border border-border bg-surface-2 px-3 text-[13px] " +
  "text-text placeholder:text-text-faint transition-colors duration-[130ms] " +
  "hover:border-border-strong focus:border-border-strong focus:outline-none " +
  "focus:ring-2 focus:ring-accent/25 disabled:opacity-45";

const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cx(CONTROL, "h-9", className)} {...rest} />;
});

export default Input;
