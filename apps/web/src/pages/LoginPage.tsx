import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { login, signup } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function LoginPage() {
  const { user, loading, refresh } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already logged in - bounce to target route (defaulting to /app)
  if (!loading && user) {
    const from = (location.state as { from?: string } | null)?.from ?? "/app";
    return <Navigate to={from} replace />;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const cleanEmail = email.trim().toLowerCase();
    if (!cleanEmail) {
      setError("Please enter your email address.");
      return;
    }

    if (mode === "signup") {
      if (password.length < 6) {
        setError("Password must be at least 6 characters long.");
        return;
      }
      if (password !== confirmPassword) {
        setError("Passwords do not match.");
        return;
      }
    }

    setSubmitting(true);
    try {
      if (mode === "signup") {
        await signup(cleanEmail, password);
      } else {
        await login(cleanEmail, password);
      }
      await refresh();
      const from = (location.state as { from?: string } | null)?.from ?? "/app";
      navigate(from, { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("409")) {
        setError("An account with this email already exists. Try signing in.");
      } else if (msg.includes("401")) {
        setError("Incorrect email or password.");
      } else if (msg.includes("422")) {
        setError("Please enter a valid email address and password.");
      } else {
        setError(mode === "signup" ? "Failed to create account. Please try again." : "Incorrect email or password.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#07090E] px-6 text-paper antialiased">
      {/* Subtle background glow */}
      <div className="pointer-events-none fixed inset-0 flex items-center justify-center">
        <div className="h-[450px] w-[450px] rounded-full bg-canonical/5 blur-[120px]" />
      </div>

      <div className="relative w-full max-w-md rounded-2xl border border-line bg-[#0E121B]/95 p-8 shadow-2xl backdrop-blur-xl">
        <div className="flex items-center justify-between">
          <Link to="/" className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-paper">
            <span>←</span> Data2plugin
          </Link>
          <span className="rounded-full border border-canonical/20 bg-canonical/10 px-2.5 py-0.5 text-[10px] font-semibold text-canonical">
            Enterprise Ready
          </span>
        </div>

        <div className="mt-5 text-left">
          <h1 className="font-display text-2xl font-bold tracking-tight text-paper">
            {mode === "login" ? "Welcome back" : "Create your account"}
          </h1>
          <p className="mt-1 text-xs text-muted">
            {mode === "login"
              ? "Sign in to manage and resume your data plugin pipelines."
              : "Start turning your enterprise datasets into Claude Desktop plugins."}
          </p>
        </div>

        {/* Tab switch */}
        <div className="mt-6 flex rounded-lg border border-line/80 bg-ink/70 p-1">
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setError(null);
            }}
            className={`flex-1 rounded-md py-1.5 text-xs font-semibold transition-all ${
              mode === "login"
                ? "bg-canonical text-ink shadow-sm"
                : "text-muted hover:text-paper"
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("signup");
              setError(null);
            }}
            className={`flex-1 rounded-md py-1.5 text-xs font-semibold transition-all ${
              mode === "signup"
                ? "bg-canonical text-ink shadow-sm"
                : "text-muted hover:text-paper"
            }`}
          >
            Create Account
          </button>
        </div>

        <form onSubmit={submit} className="mt-6 space-y-4">
          <div>
            <label className="block text-xs font-medium text-paper/80">
              Work Email
            </label>
            <input
              type="email"
              required
              autoFocus
              value={email}
              placeholder="you@company.com"
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-line bg-ink/90 px-3.5 py-2.5 text-sm text-paper placeholder:text-muted/60 transition-colors focus:border-canonical focus:outline-none focus:ring-1 focus:ring-canonical"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-paper/80">
              Password
            </label>
            <input
              type="password"
              required
              value={password}
              placeholder="••••••••"
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-line bg-ink/90 px-3.5 py-2.5 text-sm text-paper placeholder:text-muted/60 transition-colors focus:border-canonical focus:outline-none focus:ring-1 focus:ring-canonical"
            />
          </div>

          {mode === "signup" && (
            <div className="animate-in fade-in slide-in-from-top-1 duration-200">
              <label className="block text-xs font-medium text-paper/80">
                Confirm Password
              </label>
              <input
                type="password"
                required
                value={confirmPassword}
                placeholder="••••••••"
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="mt-1.5 w-full rounded-lg border border-line bg-ink/90 px-3.5 py-2.5 text-sm text-paper placeholder:text-muted/60 transition-colors focus:border-canonical focus:outline-none focus:ring-1 focus:ring-canonical"
              />
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-danger/30 bg-danger/10 px-3.5 py-2.5 text-xs text-danger">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full cursor-pointer rounded-lg bg-canonical px-4 py-2.5 text-sm font-semibold text-ink shadow-lg shadow-canonical/20 transition-all hover:scale-[1.01] hover:brightness-105 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting
              ? mode === "login"
                ? "Signing in…"
                : "Creating account…"
              : mode === "login"
              ? "Sign In to Workspace"
              : "Create Free Account"}
          </button>
        </form>

        <div className="mt-6 border-t border-line/60 pt-4 text-center">
          <p className="text-[11px] text-muted">
            {mode === "login" ? (
              <>
                Don't have an account yet?{" "}
                <button
                  type="button"
                  onClick={() => {
                    setMode("signup");
                    setError(null);
                  }}
                  className="font-medium text-canonical hover:underline"
                >
                  Create one now
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button
                  type="button"
                  onClick={() => {
                    setMode("login");
                    setError(null);
                  }}
                  className="font-medium text-canonical hover:underline"
                >
                  Sign in here
                </button>
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}
