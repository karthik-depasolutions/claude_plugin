"""FastAPI app factory. `uvicorn forge_api.main:app` or `forge-api` (the
console script installed by this package)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forge_api.config import get_settings
from forge_api.db import init_db
from forge_api.hosted_mcp import HostedMCPGateway, build_mcp_starlette
from forge_api.routers import packs, runs


def create_app() -> FastAPI:
    mcp_inner = build_mcp_starlette()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ANN201
        settings = get_settings()
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        await init_db()
        # No process-global source credential: every run carries its own (the
        # per-run credential overlay during the pipeline, and
        # `<run>/.mcp_source_env.json` for the hosted MCP gateway). A global
        # FORGE_SOURCE_DB_URL here would just be a wrong default for every run
        # that isn't the one database it points at.
        async with mcp_inner.router.lifespan_context(mcp_inner):
            yield

    app = FastAPI(title="MIS Plugin Forge API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs.router)
    app.include_router(packs.router)
    app.mount("/mcp", HostedMCPGateway(mcp_inner))

    @app.get("/health")
    async def health() -> dict:
        # `client_warehouse` tells the web app whether to offer "clone a
        # source database" - it needs the warehouse configured to have
        # somewhere to put the copy.
        return {"status": "ok", "client_warehouse": get_settings().client_warehouse_enabled}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("forge_api.main:app", host="0.0.0.0", port=8000, reload=False)
