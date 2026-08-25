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

  return (
    <div className="min-h-screen bg-ink font-sans text-paper">
      <header className="flex items-start justify-between border-b border-line px-6 py-4">
        <div>
          <Link to="/" className="text-xs text-muted transition-colors hover:text-canonical">
            ← Data2plugin
          </Link>
          <h1 className="mt-1 font-display text-lg font-semibold tracking-tight text-paper">Generate a plugin</h1>
          <p className="text-sm text-muted">Connect a data source and watch it resolve, step by step.</p>
        </div>
        {user && (
          <div className="flex items-center gap-3 text-xs text-muted">
            <span className="font-mono">{user.email}</span>
            <button type="button" onClick={handleLogout} className="transition-colors hover:text-paper">
              Log out
            </button>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Wizard />
      </main>
    </div>
  );
}
