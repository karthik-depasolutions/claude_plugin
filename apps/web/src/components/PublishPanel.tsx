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
      <div className="animate-reveal-up space-y-3 rounded-lg border border-physical/30 bg-physical/5 p-4">
        <p className="text-sm text-physical">
          Published to{" "}
          <a href={result.html_url} target="_blank" rel="noreferrer" className="underline font-semibold">
            {result.repo_full_name}
          </a>
          .
        </p>
        <div className="space-y-1">
          <p className="text-xs text-muted">
            Run these in Claude Code or Claude Desktop to install it:
          </p>
          <pre className="overflow-x-auto rounded border border-line bg-[#0E121B] p-2.5 font-mono text-xs text-paper">
            {result.marketplace_add_command}
            {"\n"}
            {result.install_command}
            {"\n/reload-plugins"}
          </pre>
        </div>
        <button
          type="button"
          onClick={() => setResult(null)}
          className="text-xs text-muted underline hover:text-paper"
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
        className="rounded border border-line bg-line/40 px-4 py-2 text-sm font-semibold text-paper transition-colors hover:border-canonical/40 hover:bg-line/80"
      >
        Publish to GitHub
      </button>
    );
  }

  return (
    <div className="animate-reveal-up space-y-3 rounded-lg border border-line bg-[#0E121B] p-4">
      <p className="text-sm text-paper/90">
        Creates a GitHub repo and pushes this plugin to it. If that name is already taken, a new
        repo is created as name-v1 (then name-v2, and so on) instead of overwriting.
      </p>
      <label className="block text-sm text-paper/80">
        Repo name
        <input
          value={repoName}
          onChange={(e) => setRepoName(e.target.value)}
          placeholder={defaultRepoName ?? "my-mis-plugin"}
          className="mt-1 w-full rounded border border-line bg-ink px-3 py-2 text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
        />
      </label>
      <label className="block text-sm text-paper/80">
        Owner (org or user — leave blank to use your GitHub account)
        <input
          value={owner}
          onChange={(e) => setOwner(e.target.value)}
          placeholder="leave blank for your account"
          className="mt-1 w-full rounded border border-line bg-ink px-3 py-2 text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
        />
      </label>
      <label className="flex items-center gap-2 text-sm text-paper/80">
        <input type="checkbox" checked={isPrivate} onChange={(e) => setIsPrivate(e.target.checked)} className="accent-canonical" />
        Private repo
      </label>

      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={submitting}
          onClick={submit}
          className="rounded bg-canonical px-4 py-2 text-sm font-semibold text-ink transition-transform hover:scale-[1.02] disabled:scale-100 disabled:opacity-40"
        >
          {submitting ? "Publishing…" : "Publish"}
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => setOpen(false)}
          className="rounded px-4 py-2 text-sm text-muted hover:text-paper"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
