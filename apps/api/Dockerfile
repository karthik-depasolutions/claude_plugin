FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/forge-core packages/forge-core
COPY packages/mcp-runtime packages/mcp-runtime
COPY apps/api apps/api
COPY industry-packs industry-packs

RUN uv sync --frozen --no-dev --extra postgres --package forge-api

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "forge_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
