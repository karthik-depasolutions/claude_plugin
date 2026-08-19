#!/usr/bin/env pwsh
<#
.SYNOPSIS
  Starts the Data2plugin API and web wizard for local testing.

.DESCRIPTION
  Opens two new PowerShell windows: one running the FastAPI service
  (uvicorn, with auto-reload) and one running the Vite dev server for the
  web wizard. Close either window (or Ctrl+C inside it) to stop that
  service.

.PARAMETER NoWeb
  Only start the API - useful if you just want to hit it with curl/the CLI.

.EXAMPLE
  ./scripts/dev-up.ps1
.EXAMPLE
  ./scripts/dev-up.ps1 -NoWeb
#>
param(
    [switch]$NoWeb
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

if (-not (Test-Path ".env")) {
    Write-Warning "No .env found - copy .env.example to .env and set GEMINI_API_KEY (or always pass --no-llm)."
}

# Prefer PowerShell 7 (pwsh) if it's installed, otherwise fall back to the
# Windows PowerShell that's guaranteed to exist on every Windows machine.
$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

# 8420/5420, not the more common 8000/5173: on a dev machine already
# running other projects' Docker containers, a port collision here doesn't
# fail loudly - uvicorn/vite just quietly bind to (or proxy to) whatever
# *else* is already listening, and every API call 404s against the wrong
# app. See apps/web/vite.config.ts for the matching proxy target.
Write-Host "Starting FastAPI on http://localhost:8420 ..." -ForegroundColor Cyan
# --reload-dir restricts the file watcher to actual source code. Without it,
# uvicorn watches the whole repo (its default is the cwd) - since every
# pipeline run writes a full plugin (including a bundled copy of
# mis-mcp-runtime) under generated/, that would trigger a full server
# restart on *every run*, killing whatever background task/SSE stream was
# live at the time.
Start-Process $shell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root'; uv run --package forge-api uvicorn forge_api.main:app --reload " +
    "--reload-dir apps/api/src --reload-dir packages/forge-core/src --reload-dir packages/mcp-runtime/src " +
    "--port 8420"
)

if (-not $NoWeb) {
    Write-Host "Starting the web wizard on http://localhost:5420 ..." -ForegroundColor Cyan
    Start-Process $shell -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$root\apps\web'; npm run dev"
    )
}

Write-Host ""
Write-Host "API docs (Swagger UI): http://localhost:8420/docs" -ForegroundColor Green
if (-not $NoWeb) {
    Write-Host "Web wizard:            http://localhost:5420" -ForegroundColor Green
}
Write-Host ""
Write-Host "Each service runs in its own window - close the window (or Ctrl+C inside it) to stop it."
Write-Host "If a window shows a port-in-use error, something else on this machine already owns that port -"
Write-Host "stop it, or change the port here and in apps/web/vite.config.ts."
