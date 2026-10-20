import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cx } from "./cx";
import { CONTROL } from "./Input";

const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cx(CONTROL, "resize-y py-2 leading-relaxed", className)} {...rest} />;
  },
);

export default Textarea;
