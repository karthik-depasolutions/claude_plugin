import { useEffect, useState } from "react";
import { getWarehouseCredentials } from "../lib/api";

/** Shown once a run that was loaded into the client warehouse succeeds -
 * this is the *only* place the real connection string is ever displayed.
 * It's held in the API process's memory only (see
 * `forge_api.registry.RunContext.warehouse_connection_string`) and is never
 * written to the jobs database, a log line, or the plugin itself, so this
 * fetch will 404 (rendering nothing) for any run that didn't go through the
 * warehouse, and forever after an API restart. */
export default function WarehouseCredentialsPanel({ runId }: { runId: string }) {
  const [connectionString, setConnectionString] = useState<string | null>(null);
  const [envVarName, setEnvVarName] = useState("FORGE_SOURCE_DB_URL");
  const [copied, setCopied] = useState(false);
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

  async function copy() {
    await navigator.clipboard.writeText(exportLine);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="rounded-lg border border-amber/40 bg-amber/5 p-4 text-doc-ink">
      <p className="text-sm">
        Your data lives in an isolated database schema. This is the <strong>only time</strong> the
        connection string is shown &mdash; copy it now and keep it somewhere safe. It&rsquo;s never saved.
      </p>
      <p className="mt-2 text-xs text-doc-ink/60">
        Set it in the environment Claude Code launches from, before first use:
      </p>
      <pre className="mt-1.5 overflow-x-auto rounded-md bg-doc-ink p-3 font-mono text-xs text-doc">{exportLine}</pre>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={copy}
          className="rounded-md border border-doc-hair px-3 py-1.5 text-xs font-medium text-doc-ink hover:bg-doc-ink/5"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          type="button"
          onClick={() => setAcknowledged(true)}
          className="px-2 py-1.5 text-xs text-doc-ink/55 underline underline-offset-4 hover:text-doc-ink"
        >
          I&rsquo;ve saved it
        </button>
      </div>
    </div>
  );
}
