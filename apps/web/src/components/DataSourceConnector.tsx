import { useState, useRef, useId } from "react";
import type { DragEvent, ChangeEvent } from "react";
import type { PackSummary } from "../lib/types";

interface DataSourceConnectorProps {
  packs?: PackSummary[];
  submitting: boolean;
  error: string | null;
  onSubmit: (payload: {
    mode: "upload" | "path" | "database";
    files?: File[];
    sourcePath?: string;
    industry?: string;
    useLlm: boolean;
    useAgent: boolean;
    label?: string;
  }) => void;
}

type DatabaseType = "postgres" | "supabase" | "neon" | "mysql" | "sqlite";

export default function DataSourceConnector({
  packs,
  submitting,
  error,
  onSubmit,
}: DataSourceConnectorProps) {
  const [activeTab, setActiveTab] = useState<"upload" | "database" | "path">("upload");

  // Multi-file state
  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Database connection builder state
  const [dbType, setDbType] = useState<DatabaseType>("postgres");
  const [dbFormMode, setDbFormMode] = useState<"guided" | "uri">("guided");
  const [dbHost, setDbHost] = useState("");
  const [dbPort, setDbPort] = useState("5432");
  const [dbName, setDbName] = useState("postgres");
  const [dbUser, setDbUser] = useState("");
  const [dbPassword, setDbPassword] = useState("");
  const [dbSchema, setDbSchema] = useState("public");
  const [dbSsl, setDbSsl] = useState(true);
  const [rawDbUri, setRawDbUri] = useState("");

  // Common config
  const [serverPath, setServerPath] = useState("");
  const [label, setLabel] = useState("");
  const [industry, setIndustry] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const [useAgent, setUseAgent] = useState(true);

  // Unique IDs for accessibility
  const uploadInputId = useId();
  const rawDbUriId = useId();
  const serverPathId = useId();
  const dbHostId = useId();
  const dbPortId = useId();
  const dbNameId = useId();
  const dbUserId = useId();
  const dbPasswordId = useId();
  const dbSchemaId = useId();
  const labelInputId = useId();
  const industrySelectId = useId();
  const useLlmCheckboxId = useId();
  const useAgentCheckboxId = useId();

  // Helper to construct DB URI from form fields
  function getConstructedDbUri(): string {
    if (dbFormMode === "uri") {
      return rawDbUri.trim();
    }
    if (dbType === "sqlite") {
      return `sqlite:///${dbName.replace(/\\/g, "/")}`;
    }
    const protocol = dbType === "mysql" ? "mysql" : "postgresql";
    const port = dbPort ? `:${dbPort}` : "";
    const userInfo = dbUser ? `${encodeURIComponent(dbUser)}${dbPassword ? `:${encodeURIComponent(dbPassword)}` : ""}@` : "";
    let uri = `${protocol}://${userInfo}${dbHost}${port}/${dbName}`;
    const params = new URLSearchParams();
    if (dbSchema && dbSchema !== "public" && protocol === "postgresql") {
      params.set("options", `-csearch_path=${dbSchema}`);
    }
    if (dbSsl && protocol === "postgresql") {
      params.set("sslmode", "require");
    }
    const query = params.toString();
    return query ? `${uri}?${query}` : uri;
  }

  // Handle drag & drop
  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addFiles(Array.from(e.dataTransfer.files));
    }
  }

  function handleFileSelect(e: ChangeEvent<HTMLInputElement>) {
    if (e.target.files && e.target.files.length > 0) {
      addFiles(Array.from(e.target.files));
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function addFiles(newFiles: File[]) {
    setFiles((prev) => {
      const existingNames = new Set(prev.map((f) => f.name));
      const filtered = newFiles.filter((f) => !existingNames.has(f.name));
      return [...prev, ...filtered];
    });
  }

  function removeFile(nameToRemove: string) {
    setFiles((prev) => prev.filter((f) => f.name !== nameToRemove));
  }

  function clearAllFiles() {
    setFiles([]);
  }

  // Format file size
  function formatBytes(bytes: number): string {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }

  // File type icon/badge helper
  function getFileBadge(filename: string): { label: string; color: string } {
    const ext = filename.split(".").pop()?.toLowerCase() ?? "";
    switch (ext) {
      case "csv":
      case "tsv":
        return { label: "CSV", color: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" };
      case "xlsx":
      case "xls":
        return { label: "EXCEL", color: "bg-green-500/15 text-green-400 border-green-500/30" };
      case "parquet":
        return { label: "PARQUET", color: "bg-purple-500/15 text-purple-400 border-purple-500/30" };
      case "json":
      case "jsonl":
      case "ndjson":
        return { label: "JSON", color: "bg-amber-500/15 text-amber-400 border-amber-500/30" };
      case "sqlite":
      case "db":
        return { label: "SQLITE", color: "bg-blue-500/15 text-blue-400 border-blue-500/30" };
      case "zip":
        return { label: "ZIP ARCHIVE", color: "bg-rose-500/15 text-rose-400 border-rose-500/30" };
      default:
        return { label: ext.toUpperCase() || "FILE", color: "bg-line text-muted border-line" };
    }
  }

  // Presets selector handler
  function selectPreset(type: DatabaseType) {
    setDbType(type);
    if (type === "supabase" || type === "neon" || type === "postgres") {
      setDbPort("5432");
      setDbSsl(true);
    } else if (type === "mysql") {
      setDbPort("3306");
      setDbSsl(false);
    }
  }

  const totalBytes = files.reduce((acc, f) => acc + f.size, 0);

  const canSubmit =
    activeTab === "upload"
      ? files.length > 0
      : activeTab === "database"
      ? dbFormMode === "uri"
        ? rawDbUri.trim().length > 0
        : (dbType === "sqlite" ? dbName.trim().length > 0 : dbHost.trim().length > 0 && dbName.trim().length > 0)
      : serverPath.trim().length > 0;

  function handleSubmit() {
    if (!canSubmit) return;
    if (activeTab === "upload") {
      onSubmit({
        mode: "upload",
        files,
        industry: industry || undefined,
        useLlm,
        useAgent: useLlm ? useAgent : false,
        label: label.trim() || undefined,
      });
    } else if (activeTab === "database") {
      const uri = getConstructedDbUri();
      onSubmit({
        mode: "database",
        sourcePath: uri,
        industry: industry || undefined,
        useLlm,
        useAgent: useLlm ? useAgent : false,
        label: label.trim() || undefined,
      });
    } else {
      onSubmit({
        mode: "path",
        sourcePath: serverPath.trim(),
        industry: industry || undefined,
        useLlm,
        useAgent: useLlm ? useAgent : false,
        label: label.trim() || undefined,
      });
    }
  }

  return (
    <div className="space-y-6">
      {/* Primary Navigation Tabs */}
      <div className="flex rounded-xl border border-line bg-surface/60 p-1.5 backdrop-blur-md">
        <button
          type="button"
          onClick={() => setActiveTab("upload")}
          className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2.5 text-xs font-semibold tracking-wide transition-all ${
            activeTab === "upload"
              ? "bg-canonical/20 text-canonical shadow-sm border border-canonical/30"
              : "text-muted hover:text-paper hover:bg-white/5"
          }`}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
          Multi-File Upload ({files.length})
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("database")}
          className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2.5 text-xs font-semibold tracking-wide transition-all ${
            activeTab === "database"
              ? "bg-physical/20 text-physical shadow-sm border border-physical/30"
              : "text-muted hover:text-paper hover:bg-white/5"
          }`}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2 1.5 3 3.5 3h9c2 0 3.5-1 3.5-3V7c0-2-1.5-3-3.5-3h-9C5.5 4 4 5 4 7zm0 4c0 2 1.5 3 3.5 3h9c2 0 3.5-1 3.5-3m-16-4c0 2 1.5 3 3.5 3h9c2 0 3.5-1 3.5-3" />
          </svg>
          Live Database Connection
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("path")}
          className={`flex flex-1 items-center justify-center gap-2 rounded-lg py-2.5 text-xs font-semibold tracking-wide transition-all ${
            activeTab === "path"
              ? "bg-paper/15 text-paper shadow-sm border border-paper/30"
              : "text-muted hover:text-paper hover:bg-white/5"
          }`}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
          </svg>
          Server Path
        </button>
      </div>

      {/* Mode 1: Multi-File Upload */}
      {activeTab === "upload" && (
        <div className="space-y-4">
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`group relative flex flex-col items-center justify-center rounded-2xl border-2 border-dashed p-8 text-center transition-all cursor-pointer ${
              isDragging
                ? "border-canonical bg-canonical/10 scale-[1.01]"
                : "border-line bg-surface/40 hover:border-canonical/50 hover:bg-surface/80"
            }`}
          >
            <input
              ref={fileInputRef}
              id={uploadInputId}
              type="file"
              multiple
              accept=".csv,.tsv,.json,.ndjson,.jsonl,.parquet,.xlsx,.xls,.sqlite,.db,.zip"
              onChange={handleFileSelect}
              className="hidden"
            />
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-canonical/15 text-canonical mb-3 group-hover:scale-110 transition-transform">
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </div>
            <p className="text-sm font-medium text-paper">
              Drag & Drop your tables here, or <span className="text-canonical underline underline-offset-4">browse files</span>
            </p>
            <p className="mt-1 text-xs text-muted">
              Select multiple CSV, Excel, Parquet, JSON, SQLite files or a .ZIP archive
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-1.5 text-[10px] text-muted">
              <span className="rounded bg-line/80 px-2 py-0.5 font-mono">.CSV</span>
              <span className="rounded bg-line/80 px-2 py-0.5 font-mono">.XLSX</span>
              <span className="rounded bg-line/80 px-2 py-0.5 font-mono">.PARQUET</span>
              <span className="rounded bg-line/80 px-2 py-0.5 font-mono">.JSON</span>
              <span className="rounded bg-line/80 px-2 py-0.5 font-mono">.SQLITE</span>
              <span className="rounded bg-line/80 px-2 py-0.5 font-mono">.ZIP</span>
            </div>
          </div>

          {/* Staged File List */}
          {files.length > 0 && (
            <div className="space-y-2 rounded-xl border border-line bg-surface/50 p-4">
              <div className="flex items-center justify-between pb-2 border-b border-line text-xs">
                <span className="font-semibold text-paper flex items-center gap-2">
                  <span>Staged Source Tables</span>
                  <span className="rounded-full bg-canonical/20 px-2 py-0.5 text-[11px] font-mono text-canonical">
                    {files.length} table{files.length === 1 ? "" : "s"} ({formatBytes(totalBytes)})
                  </span>
                </span>
                <button
                  type="button"
                  onClick={clearAllFiles}
                  className="text-muted hover:text-danger text-xs transition-colors"
                >
                  Clear all
                </button>
              </div>

              <div className="max-h-60 overflow-y-auto space-y-1.5 pr-1">
                {files.map((file) => {
                  const badge = getFileBadge(file.name);
                  const tableName = file.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_]/g, "_").toLowerCase();
                  return (
                    <div
                      key={file.name}
                      className="flex items-center justify-between rounded-lg border border-line/60 bg-base/60 px-3 py-2 text-xs transition-colors hover:border-line hover:bg-base"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <span className={`rounded border px-1.5 py-0.5 text-[10px] font-mono font-semibold ${badge.color}`}>
                          {badge.label}
                        </span>
                        <div className="truncate">
                          <span className="font-mono text-paper font-medium">{file.name}</span>
                          <span className="ml-2 text-[11px] text-muted">→ table: <code className="text-physical font-mono">{tableName}</code></span>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        <span className="text-[11px] font-mono text-muted">{formatBytes(file.size)}</span>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeFile(file.name);
                          }}
                          className="text-muted hover:text-danger transition-colors p-1"
                          title="Remove file"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="pt-2 flex justify-between items-center text-[11px] text-muted">
                <span>All tables will be ingested into a unified data schema for plugin generation.</span>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="text-canonical hover:underline font-medium"
                >
                  + Add more tables
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Mode 2: Live Database Connection */}
      {activeTab === "database" && (
        <div className="space-y-4 rounded-xl border border-line bg-surface/40 p-5">
          {/* Preset Buttons */}
          <div>
            <span className="block text-xs font-semibold text-paper/80 mb-2">Select Database Engine</span>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {[
                { id: "postgres", label: "PostgreSQL", icon: "🐘" },
                { id: "supabase", label: "Supabase", icon: "⚡" },
                { id: "neon", label: "Neon DB", icon: "🟢" },
                { id: "mysql", label: "MySQL", icon: "🐬" },
                { id: "sqlite", label: "SQLite", icon: "📁" },
              ].map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => selectPreset(preset.id as DatabaseType)}
                  className={`flex flex-col items-center justify-center gap-1 rounded-lg border p-2.5 text-xs transition-all ${
                    dbType === preset.id
                      ? "border-physical bg-physical/10 text-paper font-semibold shadow-sm"
                      : "border-line bg-base/50 text-muted hover:border-line hover:text-paper"
                  }`}
                >
                  <span className="text-base">{preset.icon}</span>
                  <span>{preset.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Form Toggle: Guided vs URI */}
          <div className="flex justify-between items-center pt-2">
            <span className="text-xs font-semibold text-paper/80">Connection Parameters</span>
            <div className="flex text-xs rounded border border-line bg-base/80 p-0.5">
              <button
                type="button"
                onClick={() => setDbFormMode("guided")}
                className={`px-2.5 py-1 rounded transition-colors ${
                  dbFormMode === "guided" ? "bg-physical/20 text-physical font-medium" : "text-muted hover:text-paper"
                }`}
              >
                Guided Form
              </button>
              <button
                type="button"
                onClick={() => setDbFormMode("uri")}
                className={`px-2.5 py-1 rounded transition-colors ${
                  dbFormMode === "uri" ? "bg-physical/20 text-physical font-medium" : "text-muted hover:text-paper"
                }`}
              >
                Raw URI
              </button>
            </div>
          </div>

          {dbFormMode === "uri" ? (
            <div className="space-y-2">
              <label htmlFor={rawDbUriId} className="block text-xs text-muted">
                Database Connection URI
              </label>
              <input
                id={rawDbUriId}
                value={rawDbUri}
                onChange={(e) => setRawDbUri(e.target.value)}
                placeholder="postgresql://user:password@aws-0-pooler.supabase.com:5432/postgres?options=-csearch_path%3Dpublic"
                className="w-full font-mono rounded-lg border border-line bg-base px-3.5 py-2.5 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
              />
              <p className="text-[11px] text-muted">
                Supports PostgreSQL, Supabase poolers, Neon, MySQL, and SQLite connection URIs.
              </p>
            </div>
          ) : dbType === "sqlite" ? (
            <div className="space-y-2">
              <label htmlFor={dbNameId} className="block text-xs text-muted">
                SQLite Database File Path
              </label>
              <input
                id={dbNameId}
                value={dbName}
                onChange={(e) => setDbName(e.target.value)}
                placeholder="d:/data/sales.db"
                className="w-full font-mono rounded-lg border border-line bg-base px-3.5 py-2.5 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
              />
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label htmlFor={dbHostId} className="block text-xs text-muted">
                  Host / Server Address
                </label>
                <input
                  id={dbHostId}
                  value={dbHost}
                  onChange={(e) => setDbHost(e.target.value)}
                  placeholder="e.g. aws-0-ap-northeast-1.pooler.supabase.com"
                  className="w-full font-mono rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <label htmlFor={dbPortId} className="block text-xs text-muted">
                    Port
                  </label>
                  <input
                    id={dbPortId}
                    value={dbPort}
                    onChange={(e) => setDbPort(e.target.value)}
                    placeholder="5432"
                    className="w-full font-mono rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
                  />
                </div>
                <div className="space-y-1">
                  <label htmlFor={dbNameId} className="block text-xs text-muted">
                    Database Name
                  </label>
                  <input
                    id={dbNameId}
                    value={dbName}
                    onChange={(e) => setDbName(e.target.value)}
                    placeholder="postgres"
                    className="w-full font-mono rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <label htmlFor={dbUserId} className="block text-xs text-muted">
                  User / Username
                </label>
                <input
                  id={dbUserId}
                  value={dbUser}
                  onChange={(e) => setDbUser(e.target.value)}
                  placeholder="e.g. postgres.cvpizqmzlpcjkwdrytsr"
                  className="w-full font-mono rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label htmlFor={dbPasswordId} className="block text-xs text-muted">
                  Password
                </label>
                <input
                  id={dbPasswordId}
                  type="password"
                  value={dbPassword}
                  onChange={(e) => setDbPassword(e.target.value)}
                  placeholder="••••••••••••"
                  className="w-full font-mono rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label htmlFor={dbSchemaId} className="block text-xs text-muted">
                  Schema / Search Path
                </label>
                <input
                  id={dbSchemaId}
                  value={dbSchema}
                  onChange={(e) => setDbSchema(e.target.value)}
                  placeholder="public"
                  className="w-full font-mono rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-physical focus:outline-none"
                />
              </div>

              <div className="flex items-center pt-5">
                <label className="flex items-center gap-2 text-xs text-paper cursor-pointer">
                  <input
                    type="checkbox"
                    checked={dbSsl}
                    onChange={(e) => setDbSsl(e.target.checked)}
                    className="accent-physical rounded"
                  />
                  Require SSL Connection (<code className="text-muted">sslmode=require</code>)
                </label>
              </div>
            </div>
          )}

          {/* Computed URI Preview */}
          {dbFormMode === "guided" && dbHost && (
            <div className="rounded-lg border border-line/60 bg-base/80 p-3 text-[11px]">
              <span className="text-muted block mb-1">Target Connection Target:</span>
              <code className="font-mono text-physical break-all">
                {getConstructedDbUri().replace(/:([^:@]+)@/, ":••••••@")}
              </code>
            </div>
          )}
        </div>
      )}

      {/* Mode 3: Server Path */}
      {activeTab === "path" && (
        <div className="space-y-2 rounded-xl border border-line bg-surface/40 p-5">
          <label htmlFor={serverPathId} className="block text-xs font-semibold text-paper/80">
            Absolute Server File or Directory Path
          </label>
          <input
            id={serverPathId}
            value={serverPath}
            onChange={(e) => setServerPath(e.target.value)}
            placeholder="e.g. /var/data/hospital_records/ or d:\data\sales_dataset\"
            className="w-full font-mono rounded-lg border border-line bg-base px-3.5 py-2.5 text-xs text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
          />
          <p className="text-[11px] text-muted">
            Point to a single file (.csv, .xlsx, .parquet, .sqlite) or an entire directory of tables.
          </p>
        </div>
      )}

      {/* Options Accordion / Common Settings */}
      <div className="rounded-xl border border-line bg-surface/40 p-5 space-y-4">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
          Pipeline Configuration
        </h3>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1">
            <label htmlFor={labelInputId} className="block text-xs font-medium text-paper/80">
              Project / Brand Name (optional)
            </label>
            <input
              id={labelInputId}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Sparda Music Academy"
              className="w-full rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper placeholder:text-muted focus:border-canonical focus:outline-none"
            />
            <span className="text-[11px] text-muted block">
              Used to personalize plugin naming and dedicated database schemas.
            </span>
          </div>

          <div className="space-y-1">
            <label htmlFor={industrySelectId} className="block text-xs font-medium text-paper/80">
              Industry Knowledge Pack
            </label>
            <select
              id={industrySelectId}
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              className="w-full rounded-lg border border-line bg-base px-3 py-2 text-xs text-paper focus:border-canonical focus:outline-none"
            >
              <option value="">✨ Auto-detect Industry via Reasoning Agent</option>
              {packs?.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-muted block">
              Auto-detects semantic KPIs and domain ontology from table patterns.
            </span>
          </div>
        </div>

        {/* AI & Agentic Controls */}
        <div className="pt-2 border-t border-line/60 space-y-2.5">
          <label htmlFor={useLlmCheckboxId} className="flex items-center gap-2.5 text-xs text-paper/90 cursor-pointer">
            <input
              id={useLlmCheckboxId}
              type="checkbox"
              checked={useLlm}
              onChange={(e) => {
                setUseLlm(e.target.checked);
                if (!e.target.checked) setUseAgent(false);
              }}
              className="accent-canonical rounded w-4 h-4"
            />
            <span>Enable LLM Reasoning (Semantic Profiling, Metric Proposals & Self-Critique)</span>
          </label>

          {useLlm && (
            <label htmlFor={useAgentCheckboxId} className="flex items-center gap-2.5 pl-6 text-xs text-paper/80 cursor-pointer">
              <input
                id={useAgentCheckboxId}
                type="checkbox"
                checked={useAgent}
                onChange={(e) => setUseAgent(e.target.checked)}
                className="accent-canonical rounded w-4 h-4"
              />
              <span className="flex items-center gap-2">
                <span>Agentic Data Investigation & Self-Correction</span>
                <span className="rounded bg-physical/15 px-2 py-0.5 text-[10px] font-mono text-physical border border-physical/30 font-semibold">
                  AST Safe
                </span>
              </span>
            </label>
          )}
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="rounded-xl border border-danger/40 bg-danger/10 p-3 text-xs text-danger flex items-center gap-2">
          <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{error}</span>
        </div>
      )}

      {/* Launch Action */}
      <div className="flex items-center justify-between pt-2">
        <div className="text-xs text-muted">
          {activeTab === "upload" && (
            <span>
              {files.length === 0
                ? "Select at least 1 table file to proceed"
                : `Ready to ingest ${files.length} table${files.length > 1 ? "s" : ""} (${formatBytes(totalBytes)})`}
            </span>
          )}
          {activeTab === "database" && <span>Live connection will be validated against database</span>}
          {activeTab === "path" && <span>Filesystem source path will be scanned for schema tables</span>}
        </div>

        <button
          type="button"
          disabled={!canSubmit || submitting}
          onClick={handleSubmit}
          className="relative inline-flex items-center gap-2.5 rounded-xl bg-gradient-to-r from-physical to-emerald-400 px-6 py-3 text-xs font-bold uppercase tracking-wider text-ink shadow-lg shadow-physical/20 transition-all hover:scale-[1.02] hover:shadow-physical/30 active:scale-[0.98] disabled:scale-100 disabled:opacity-40 disabled:pointer-events-none cursor-pointer"
        >
          {submitting ? (
            <>
              <svg className="w-4 h-4 animate-spin text-ink" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span>Starting Pipeline…</span>
            </>
          ) : (
            <>
              <span>Generate MIS Plugin</span>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M14 5l7 7m0 0l-7 7m7-7H3" />
              </svg>
            </>
          )}
        </button>
      </div>
    </div>
  );
}
