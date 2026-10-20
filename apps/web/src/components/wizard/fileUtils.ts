/** Accepted upload extensions — mirrors the `accept` attr and the backend's
 *  ingest registry. Kept here so the dropzone and its validation agree. */
export const ACCEPT_EXTENSIONS = [
  ".csv",
  ".tsv",
  ".json",
  ".ndjson",
  ".parquet",
  ".xlsx",
  ".xls",
  ".sqlite",
  ".db",
  ".zip",
] as const;

export const ACCEPT_ATTR = ACCEPT_EXTENSIONS.join(",");

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

export function isAccepted(name: string): boolean {
  return (ACCEPT_EXTENSIONS as readonly string[]).includes(extensionOf(name));
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let n = bytes / 1024;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

const key = (f: File) => `${f.name}:${f.size}`;

/** Merge picks into the existing selection, de-duping by name+size. */
export function mergeFiles(current: File[], incoming: File[]): File[] {
  const seen = new Set(current.map(key));
  const added = incoming.filter((f) => !seen.has(key(f)));
  return [...current, ...added];
}

export interface Partitioned {
  accepted: File[];
  rejected: { name: string; reason: string }[];
}

export function partition(incoming: File[]): Partitioned {
  const accepted: File[] = [];
  const rejected: { name: string; reason: string }[] = [];
  for (const f of incoming) {
    if (isAccepted(f.name)) accepted.push(f);
    else rejected.push({ name: f.name, reason: `${extensionOf(f.name) || "no extension"} not supported` });
  }
  return { accepted, rejected };
}
