import { useState } from "react";
import { publishToGithub } from "../lib/api";
import type { PublishGithubResponse } from "../lib/types";
import Button from "./ui/Button";
import CopyButton from "./ui/CopyButton";
import Field from "./ui/Field";
import Input from "./ui/Input";

/** Shown once a run has succeeded. Creates a new GitHub repo for the plugin and
 *  pushes it, so anyone can install it into Claude Code from that repo. */
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

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      setResult(
        await publishToGithub(runId, {
          repoName: repoName.trim() || undefined,
          owner: owner.trim() || undefined,
          private: isPrivate,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    const commands = `${result.marketplace_add_command}\n${result.install_command}\n/reload-plugins`;
    return (
      <div className="rounded-[var(--radius)] border border-border border-l-2 border-l-ok bg-surface p-4">
        <p className="text-[13px] text-text">
          Published to{" "}
          <a
            href={result.html_url}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-text underline underline-offset-4"
          >
            {result.repo_full_name}
          </a>
        </p>
        <pre className="mt-2 overflow-x-auto rounded-[var(--radius-sm)] border border-border bg-surface-2 p-3 font-mono text-[12px] text-text-muted">
          {commands}
        </pre>
        <div className="mt-2 flex items-center gap-2">
          <CopyButton value={commands} label="Copy commands" />
          <Button type="button" variant="ghost" size="sm" onClick={() => setResult(null)}>
            Publish to a different repo
          </Button>
        </div>
      </div>
    );
  }

  if (!open) {
    return (
      <Button type="button" variant="secondary" onClick={() => setOpen(true)}>
        Publish to GitHub
      </Button>
    );
  }

  return (
    <div className="rounded-[var(--radius)] border border-border bg-surface p-4">
      <p className="text-[13px] leading-relaxed text-text-muted">
        Creates a GitHub repo and pushes this plugin. If the name is taken it makes name-v1, name-v2,
        &hellip; rather than overwriting.
      </p>
      <div className="mt-3 space-y-3">
        <Field label="Repo name">
          <Input
            value={repoName}
            onChange={(e) => setRepoName(e.target.value)}
            placeholder={defaultRepoName ?? "my-mis-plugin"}
          />
        </Field>
        <Field label="Owner" hint="org or user; blank = your account">
          <Input
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="blank for your account"
          />
        </Field>
        <label className="flex items-center gap-2 text-[13px] text-text-muted">
          <input
            type="checkbox"
            checked={isPrivate}
            onChange={(e) => setIsPrivate(e.target.checked)}
            className="h-3.5 w-3.5 accent-accent"
          />
          Private repo
        </label>
      </div>

      {error && (
        <p className="mt-2 text-[13px] text-danger" role="alert">
          {error}
        </p>
      )}

      <div className="mt-4 flex gap-2">
        <Button type="button" variant="primary" size="sm" loading={submitting} onClick={submit}>
          {submitting ? "Publishing…" : "Publish"}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={submitting}
          onClick={() => setOpen(false)}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
