FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml .
COPY packages/ packages/
COPY workers/ workers/
COPY industry-packs/ industry-packs/
COPY fixtures/ fixtures/

RUN uv pip install --system -e packages/forge-core -e .

CMD ["python", "workers/temporal_worker/worker.py"]
