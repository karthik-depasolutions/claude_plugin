import { useState } from "react";
import { publishToGithub } from "../lib/api";
import type { PublishGithubResponse } from "../lib/types";

/** Shown once a run has succeeded - lets the user create a brand-new GitHub
 * repo for the just-generated plugin and push it there in one step, so
 * anyone can install it into Claude Code/Desktop straight from that repo
 * (no separate marketplace repo required first - see
 * forge_core.publishing.standalone_repo). */
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
  // Public by default - a private repo needs the installer to also be
  // granted GitHub access before Claude Desktop's "Add marketplace" can
  // see it, which is an extra, easy-to-get-stuck-on step for what's meant
  // to be a quick "try this plugin" flow. Toggle it on for anything with
  // business-sensitive KPI/schema logic worth restricting.
  const [isPrivate, setIsPrivate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PublishGithubResponse | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const response = await publishToGithub(runId, {
        repoName: repoName.trim() || undefined,
        owner: owner.trim() || undefined,
        private: isPrivate,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <div className="space-y-3 rounded border border-emerald-800/50 bg-emerald-950/20 p-4">
        <p className="text-sm text-emerald-300">
          Published to{" "}
          <a href={result.html_url} target="_blank" rel="noreferrer" className="underline">
            {result.repo_full_name}
          </a>
          .
        </p>
        <div className="space-y-1">
          <p className="text-xs text-slate-400">
            Run these in Claude Code or Claude Desktop to install it:
          </p>
          <pre className="overflow-x-auto rounded bg-slate-950 p-2 text-xs text-slate-200">
            {result.marketplace_add_command}
            {"\n"}
            {result.install_command}
            {"\n/reload-plugins"}
          </pre>
        </div>
        <button
          type="button"
          onClick={() => setResult(null)}
          className="text-xs text-slate-400 underline hover:text-slate-200"
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
        className="rounded border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 hover:bg-slate-800"
      >
        Publish to GitHub
      </button>
    );
  }

  return (
    <div className="space-y-3 rounded border border-slate-700 bg-slate-900/50 p-4">
      <p className="text-sm text-slate-300">
        Creates a GitHub repo and pushes this plugin to it. If that name is already taken, a new
        repo is created as name-v1 (then name-v2, and so on) instead of overwriting.
      </p>
      <label className="block text-sm text-slate-300">
        Repo name
        <input
          value={repoName}
          onChange={(e) => setRepoName(e.target.value)}
          placeholder={defaultRepoName ?? "my-mis-plugin"}
          className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2"
        />
      </label>
      <label className="block text-sm text-slate-300">
        Owner (org or user — leave blank to use your GitHub account)
        <input
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          placeholder="leave blank for your account"
          className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2"
        />
      </label>
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} />
        Private repo
      </label>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={submitting}
          onClick={submit}
          className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {submitting ? "Publishing…" : "Publish"}
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => setOpen(false)}
          className="rounded px-4 py-2 text-sm text-slate-400 hover:text-slate-200"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
