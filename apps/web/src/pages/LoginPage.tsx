import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { login } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function LoginPage() {
  const { user, loading, refresh } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already logged in (or just finished checking and found a session) -
  // bounce straight to wherever RequireAuth sent them from, defaulting to /app.
  if (!loading && user) {
    const from = (location.state as { from?: string } | null)?.from ?? "/app";
    return <Navigate to={from} replace />;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      await refresh();
      const from = (location.state as { from?: string } | null)?.from ?? "/app";
      navigate(from, { replace: true });
    } catch {
      setError("Incorrect email or password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink px-6 text-paper">
      <div className="w-full max-w-sm rounded-xl border border-line bg-[#0E121B] p-6 shadow-2xl">
        <Link to="/" className="text-xs text-muted hover:text-paper">
          ← Data2plugin
        </Link>
        <h1 className="mt-3 font-display text-xl font-semibold tracking-tight text-paper">Log in</h1>
        <p className="mt-1 text-xs text-muted">Accounts are provisioned by an admin — ask for access if you don't have one.</p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <label className="block text-sm text-paper/80">
            Email
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded border border-line bg-ink px-3 py-2 text-sm text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
            />
          </label>
          <label className="block text-sm text-paper/80">
            Password
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5 w-full rounded border border-line bg-ink px-3 py-2 text-sm text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
            />
          </label>

          {error && <p className="text-sm text-danger">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-canonical px-4 py-2.5 text-sm font-semibold text-ink transition-transform hover:scale-[1.02] disabled:scale-100 disabled:opacity-40"
          >
            {submitting ? "Logging in…" : "Log in"}
          </button>
        </form>
      </div>
    </div>
  );
}
