import { useEffect, useState } from "react";
import { getWarehouseCredentials } from "../lib/api";
import Button from "./ui/Button";
import CopyButton from "./ui/CopyButton";

/** Shown once a run that was loaded into the client warehouse succeeds — the
 *  *only* place the real connection string is ever displayed. It lives in the
 *  API process's memory only, so this fetch 404s (rendering nothing) for any
 *  run that didn't go through the warehouse, and forever after an API restart. */
export default function WarehouseCredentialsPanel({ runId }: { runId: string }) {
  const [connectionString, setConnectionString] = useState<string | null>(null);
  const [envVarName, setEnvVarName] = useState("FORGE_SOURCE_DB_URL");
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getWarehouseCredentials(runId).then((result) => {
      if (cancelled || !result) return;
      setConnectionString(result.connection_string);
      setEnvVarName(result.env_var_name);
    });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!connectionString || acknowledged) return null;

  const exportLine = `export ${envVarName}="${connectionString}"`;

  return (
    <div className="rounded-[var(--radius)] border border-border border-l-2 border-l-warn bg-surface p-4">
      <p className="text-[13px] leading-relaxed text-text">
        Your data lives in an isolated database schema. This is the <strong>only time</strong> the
        connection string is shown — copy it now and keep it somewhere safe. It&rsquo;s never saved.
      </p>
      <p className="mt-2 text-[12px] text-text-faint">
        Set it in the environment Claude Code launches from, before first use:
      </p>
      <pre className="mt-1.5 overflow-x-auto rounded-[var(--radius-sm)] border border-border bg-surface-2 p-3 font-mono text-[12px] text-text-muted">
        {exportLine}
      </pre>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <CopyButton value={exportLine} />
        <Button type="button" variant="ghost" size="sm" onClick={() => setAcknowledged(true)}>
          I&rsquo;ve saved it
        </Button>
      </div>
    </div>
  );
}
