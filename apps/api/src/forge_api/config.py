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

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    return Settings()
