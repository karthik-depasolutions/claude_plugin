import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cx } from "./cx";
import Spinner from "./Spinner";

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-[var(--radius-sm)] font-medium " +
  "whitespace-nowrap transition-colors duration-[130ms] focus-visible:outline-none " +
  "focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 " +
  "focus-visible:ring-offset-bg disabled:cursor-not-allowed";

const VARIANT: Record<Variant, string> = {
  // disabled primary reads as "waiting for input", not a broken white bar
  primary:
    "bg-accent text-accent-ink hover:bg-accent-hover " +
    "disabled:bg-surface-2 disabled:text-text-faint",
  secondary:
    "border border-border bg-transparent text-text hover:border-border-strong hover:bg-surface-2 " +
    "disabled:opacity-45",
  ghost: "text-text-muted hover:bg-surface-2 hover:text-text disabled:opacity-45",
};

const SIZE: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-3.5 text-[13px]",
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading = false, disabled, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(BASE, VARIANT[variant], SIZE[size], className)}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
});

export default Button;
