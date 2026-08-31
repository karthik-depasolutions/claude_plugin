import { useState } from "react";
import { publishToGithub } from "../lib/api";
import type { PublishGithubResponse } from "../lib/types";

/** Shown once a run has succeeded (on the "paper" surface). Creates a new
 * GitHub repo for the plugin and pushes it, so anyone can install it into
 * Claude Code straight from that repo. */
export default function PublishPanel({
  runId,
  defaultRepoName,
}: {
  runId: string;
  defaultRepoName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [repoName, setRepoName] = useState(defaultRepoName ?? "");
  const [owner, setOwner] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PublishGithubResponse | null>(null);

  const field =
    "mt-1 w-full rounded-md border border-doc-hair bg-white/60 px-3 py-2 text-sm text-doc-ink placeholder:text-doc-ink/35 focus:border-amber focus:outline-none focus:ring-2 focus:ring-amber/25";

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      setResult(
        await publishToGithub(runId, {
          repoName: repoName.trim() || undefined,
          owner: owner.trim() || undefined,
          private: isPrivate,
        })
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <div className="rounded-lg border border-jade/40 bg-jade/5 p-4 text-doc-ink">
        <p className="text-sm">
          Published to{" "}
          <a href={result.html_url} target="_blank" rel="noreferrer" className="font-medium underline">
            {result.repo_full_name}
          </a>
        </p>
        <pre className="mt-2 overflow-x-auto rounded-md bg-doc-ink p-3 font-mono text-xs text-doc">
          {result.marketplace_add_command}
          {"\n"}
          {result.install_command}
          {"\n/reload-plugins"}
        </pre>
        <button
          type="button"
          onClick={() => setResult(null)}
          className="mt-2 text-xs text-doc-ink/55 underline underline-offset-4 hover:text-doc-ink"
        >
          Publish to a different repo
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md border border-doc-hair px-4 py-2 text-sm font-medium text-doc-ink hover:bg-doc-ink/5"
      >
        Publish to GitHub
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-doc-hair p-4 text-doc-ink">
      <p className="text-sm text-doc-ink/70">
        Creates a GitHub repo and pushes this plugin. If the name is taken, it makes name-v1, name-v2,
        &hellip; rather than overwriting.
      </p>
      <label className="mt-3 block text-sm font-medium">
        Repo name
        <input value={repoName} onChange={(e) => setRepoName(e.target.value)} placeholder={defaultRepoName ?? "my-mis-plugin"} className={field} />
      </label>
      <label className="mt-3 block text-sm font-medium">
        Owner <span className="font-normal text-doc-ink/50">org or user; blank = your account</span>
        <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="blank for your account" className={field} />
      </label>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} />
        Private repo
      </label>

      {error && <p className="mt-2 text-sm text-clay">{error}</p>}

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          disabled={submitting}
          onClick={submit}
          className="rounded-md bg-doc-ink px-4 py-2 text-sm font-medium text-doc transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {submitting ? "Publishing…" : "Publish"}
        </button>
        <button type="button" disabled={submitting} onClick={() => setOpen(false)} className="px-3 py-2 text-sm text-doc-ink/55 hover:text-doc-ink">
          Cancel
        </button>
      </div>
    </div>
  );
}
