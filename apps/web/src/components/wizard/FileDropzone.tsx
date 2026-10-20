import { useId, useRef, useState } from "react";
import { cx } from "../ui/cx";
import Button from "../ui/Button";
import FileRow from "./FileRow";
import { ACCEPT_ATTR, humanSize, mergeFiles, partition } from "./fileUtils";

interface Props {
  files: File[];
  onChange: (files: File[]) => void;
  /** disables interaction while the run POST is in flight */
  processing?: boolean;
}

/** The upload surface. Distinct states: empty · dragging · selected · error ·
 *  processing. Drag-and-drop, click-to-browse, per-file remove, zip-aware. */
export default function FileDropzone({ files, onChange, processing = false }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<{ name: string; reason: string }[]>([]);
  const statusId = useId();

  function ingest(list: FileList | null) {
    if (!list || processing) return;
    const { accepted, rejected: bad } = partition(Array.from(list));
    setRejected(bad);
    if (accepted.length) onChange(mergeFiles(files, accepted));
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    ingest(e.dataTransfer.files);
  }

  const hasZip = files.some((f) => f.name.toLowerCase().endsWith(".zip"));
  const zipMixed = hasZip && files.length > 1;
  const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
  const empty = files.length === 0;

  return (
    <div>
      <div
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          if (!processing) setDragging(true);
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={(e) => {
          e.preventDefault();
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) setDragging(false);
        }}
        onDrop={onDrop}
        className={cx(
          "rounded-[var(--radius)] border border-dashed px-4 py-5 transition-colors duration-[130ms]",
          dragging ? "border-border-strong bg-accent/[0.05]" : "border-border-strong/60",
          processing && "pointer-events-none opacity-60",
        )}
      >
        {empty ? (
          <div className="flex flex-col items-center gap-2.5 py-3 text-center">
            <p className="text-[13px] text-text-muted">
              Drop your tables here, or a single <span className="font-mono text-text">.zip</span>
            </p>
            <Button type="button" size="sm" onClick={() => inputRef.current?.click()}>
              Browse files
            </Button>
            <p className="font-mono text-[10.5px] text-text-faint">
              csv · tsv · json · ndjson · parquet · xlsx · sqlite · db · zip
            </p>
          </div>
        ) : (
          <div>
            <ul className="divide-y divide-border">
              {files.map((f, i) => (
                <FileRow
                  key={`${f.name}:${f.size}`}
                  file={f}
                  disabled={processing}
                  onRemove={() => onChange(files.filter((_, j) => j !== i))}
                />
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-3">
              <span className="font-mono text-[11px] text-text-faint">
                {files.length} file{files.length === 1 ? "" : "s"} · {humanSize(totalBytes)}
              </span>
              <span className="flex-1" />
              <Button type="button" variant="ghost" size="sm" onClick={() => inputRef.current?.click()}>
                Add files
              </Button>
              <Button type="button" variant="ghost" size="sm" onClick={() => onChange([])}>
                Clear
              </Button>
            </div>
          </div>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTR}
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          ingest(e.target.files);
          e.target.value = "";
        }}
      />

      <div id={statusId} aria-live="polite" className="mt-2 space-y-1">
        {zipMixed && (
          <p className="font-mono text-[11px] text-warn">
            ! a .zip is uploaded on its own — remove the other files or the zip
          </p>
        )}
        {rejected.map((r) => (
          <p key={r.name} className="font-mono text-[11px] text-danger">
            ✕ {r.name} — {r.reason}
          </p>
        ))}
      </div>
    </div>
  );
}
