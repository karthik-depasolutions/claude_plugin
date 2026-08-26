"""FastAPI app factory. `uvicorn forge_api.main:app` or `forge-api` (the
console script installed by this package)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from forge_api.config import get_settings
from forge_api.db import init_db
from forge_api.hosted_mcp import HostedMCPGateway, build_mcp_starlette
from forge_api.routers import auth, packs, runs


def create_app() -> FastAPI:
    mcp_inner = build_mcp_starlette()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ANN201
        settings = get_settings()
        settings.runs_dir.mkdir(parents=True, exist_ok=True)
        await init_db()
        from forge_api import pipeline_runner
        await pipeline_runner.startup_cleanup_zombie_runs()
        if settings.client_warehouse_url:
            os.environ.setdefault("FORGE_SOURCE_DB_URL", settings.client_warehouse_url)
        async with mcp_inner.router.lifespan_context(mcp_inner):
            yield

    app = FastAPI(title="Data2plugin API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,  # the login session travels as a cookie
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(runs.router)
    app.include_router(packs.router)
    app.mount("/mcp", HostedMCPGateway(mcp_inner))

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("forge_api.main:app", host="0.0.0.0", port=8000, reload=False)
