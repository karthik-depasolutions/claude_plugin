"""Environment-driven settings. Mirrors the `FORGE_*` variables documented in
`.env.example` at the repo root."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FORGE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./generated/forge.db"
    runs_dir: Path = Path("./generated/runs")
    api_cors_origins: str = "http://localhost:5173"
    # Public origin Claude Desktop uses to reach hosted MCP (HTTPS in prod;
    # a tunnel URL for local demos). Required for GitHub publish.
    public_base_url: str | None = None

    # Client data warehouse (see forge_core.ingestion.warehouse). Feature is
    # off - uploads are saved to local files exactly as before - unless
    # `client_warehouse_url` (the *admin* connection, reachable only from
    # wherever this process runs, e.g. via an SSH tunnel to the staging box)
    # is configured. `client_warehouse_public_*` is the network-reachable
    # address baked into the connection string a client's plugin actually
    # uses - almost always different from the admin URL's host/port.
    client_warehouse_url: str | None = None
    client_warehouse_public_host: str | None = None
    client_warehouse_public_port: int = 5432
    client_warehouse_database: str = "forge_warehouse"
    # Set only when `client_warehouse_public_host` is a pooler that needs the
    # target project encoded in the username (e.g. Supabase's pooler wants
    # `<role>.<project_ref>`) - see provision_client_schema's docstring.
    client_warehouse_public_username_suffix: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def client_warehouse_enabled(self) -> bool:
        return bool(self.client_warehouse_url and self.client_warehouse_public_host)


def get_settings() -> Settings:
    return Settings()
