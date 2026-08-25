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
    <div className="space-y-3 rounded-lg border border-attention/30 bg-attention/5 p-4">
      <p className="text-sm text-attention">
        Your data was loaded into a dedicated, isolated database schema. This is the{" "}
        <strong>only time</strong> this connection string is shown — copy it now and store it
        somewhere safe (e.g. a password manager). We never save it.
      </p>
      <p className="text-xs text-muted">
        Set this in the environment Claude Desktop/Code launches from, before first use:
      </p>
      <pre className="overflow-x-auto rounded border border-line bg-[#0E121B] p-2.5 font-mono text-xs text-paper">{exportLine}</pre>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={copy}
          className="rounded border border-line bg-line/40 px-3 py-1.5 text-xs font-medium text-paper transition-colors hover:border-canonical/40 hover:bg-line/80"
        >
          {copied ? "Copied!" : "Copy to clipboard"}
        </button>
        <button
          type="button"
          onClick={() => setAcknowledged(true)}
          className="rounded px-3 py-1.5 text-xs text-muted underline hover:text-paper"
        >
          I've saved it, hide this
        </button>
      </div>
    </div>
  );
}
