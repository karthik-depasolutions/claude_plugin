#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=== MIS Plugin Forge - Startup ==="

if [ ! -f .env ]; then
    echo "Creating .env from .env.example (edit before running if you need custom settings)"
    cp .env.example .env
fi

echo "Installing dependencies..."
uv sync --all-packages --dev

echo ""
echo "Starting FastAPI API on http://localhost:8420"
uv run --package forge-api uvicorn forge_api.main:app --reload \
    --reload-dir apps/api/src \
    --reload-dir packages/forge-core/src \
    --reload-dir packages/mcp-runtime/src \
    --port 8420 &

echo "Starting web wizard on http://localhost:5420"
cd apps/web
npm install
npm run dev &

echo ""
echo "=== Ready ==="
echo "API docs : http://localhost:8420/docs"
echo "Web UI   : http://localhost:5420"
echo "Ctrl+C to stop all services"
wait
