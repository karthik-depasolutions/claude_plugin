/** Join truthy class strings. No dependency; the whole helper is one line. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
