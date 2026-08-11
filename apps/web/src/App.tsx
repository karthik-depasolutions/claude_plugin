import Wizard from "./pages/Wizard";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">MIS Plugin Forge</h1>
        <p className="text-sm text-slate-400">Generate a Claude Code plugin from your MIS data.</p>
      </header>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Wizard />
      </main>
    </div>
  );
}
