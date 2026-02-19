# Stage 1: Build all wheels
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY . .

# Build every workspace package into /app/dist/
RUN mkdir -p /app/dist && \
    for pkg in packages/*/; do \
        uv build --wheel "$pkg" --out-dir /app/dist; \
    done && \
    uv build --wheel . --out-dir /app/dist

# Stage 2: Clean runtime image — no uv, no source
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install all wheels (sub-packages first, then deepctl main)
COPY --from=builder /app/dist /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

ENTRYPOINT ["deepctl"]
CMD ["--help"]
