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
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex items-start justify-between border-b border-slate-800 px-6 py-4">
        <div>
          <Link to="/" className="text-xs text-slate-500 hover:text-slate-300">
            ← Data2plugin
          </Link>
          <h1 className="mt-1 text-lg font-semibold tracking-tight">Generate a plugin</h1>
          <p className="text-sm text-slate-400">Connect a data source and walk through the pipeline.</p>
        </div>
        {user && (
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span>{user.email}</span>
            <button type="button" onClick={handleLogout} className="text-slate-400 hover:text-slate-200">
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
