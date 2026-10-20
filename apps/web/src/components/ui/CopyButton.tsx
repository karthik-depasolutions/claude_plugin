import { useState } from "react";
import Button, { type ButtonProps } from "./Button";

interface Props extends Omit<ButtonProps, "children" | "onClick"> {
  value: string;
  label?: string;
}

/** Copy `value` to the clipboard; the label flips to "Copied" for 2s. */
export default function CopyButton({ value, label = "Copy", size = "sm", ...rest }: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — nothing better to do here than leave the label */
    }
  }

  return (
    <Button type="button" size={size} onClick={copy} {...rest}>
      {copied ? "Copied" : label}
    </Button>
  );
}
