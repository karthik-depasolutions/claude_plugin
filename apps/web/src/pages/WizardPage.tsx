import { Link } from "react-router-dom";
import Wizard from "./Wizard";

export default function WizardPage() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <Link to="/" className="text-xs text-slate-500 hover:text-slate-300">
          ← MIS Plugin Forge
        </Link>
        <h1 className="mt-1 text-lg font-semibold tracking-tight">Generate a plugin</h1>
        <p className="text-sm text-slate-400">Connect a data source and walk through the pipeline.</p>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Wizard />
      </main>
    </div>
  );
}
