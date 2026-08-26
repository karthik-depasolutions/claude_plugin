import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import Wizard from "./Wizard";

export default function WizardPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  const initial = (user?.email || "U").charAt(0).toUpperCase();

  return (
    <div className="min-h-screen bg-[#07090E] font-sans text-paper antialiased">
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-[#0E121B]/95 px-6 py-3.5 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <Link to="/" className="flex items-center gap-2 group">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-canonical/10 border border-canonical/30 text-canonical font-bold text-xs group-hover:scale-105 transition-transform">
              ⚡
            </div>
            <span className="font-display font-bold text-sm tracking-tight text-paper group-hover:text-canonical transition-colors">
              Data2plugin
            </span>
          </Link>
          <span className="hidden sm:inline-block rounded-full border border-line bg-ink px-2 py-0.5 text-[10px] font-mono text-muted">
            v0.2.0 · Claude 3.7
          </span>
        </div>

        {user && (
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 rounded-full border border-line bg-ink px-3 py-1 text-xs">
              <div className="flex h-5 w-5 items-center justify-center rounded-full bg-canonical text-ink font-bold text-[10px]">
                {initial}
              </div>
              <span className="font-mono text-xs text-paper/90 max-w-[180px] sm:max-w-[260px] truncate">
                {user.email}
              </span>
              {user.is_admin && (
                <span className="rounded bg-amber-500/20 border border-amber-500/30 px-1.5 py-0.2 text-[9px] font-bold text-amber-300 uppercase tracking-wider">
                  Admin
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={handleLogout}
              className="rounded-lg border border-line bg-ink/80 px-2.5 py-1 text-xs text-muted hover:border-danger/40 hover:text-danger transition-colors cursor-pointer"
            >
              Log out
            </button>
          </div>
        )}
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <Wizard />
      </main>
    </div>
  );
}
