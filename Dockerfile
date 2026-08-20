# Stage 1: build the dashboard SPA
FROM node:22-slim AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./web/
RUN cd web && npm ci
COPY web ./web
COPY src/cortex/server/web ./src/cortex/server/web
RUN cd web && npm run build

# Stage 2: the app
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/cortex
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=web /build/src/cortex/server/webdist ./src/cortex/server/webdist
RUN pip install --no-cache-dir .

# The brain lives in a volume; SETUP flags create it on first run.
ENV CORTEX_BRAIN=/brain
EXPOSE 8642
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8642/health')"
CMD ["cortex", "serve", "--host", "0.0.0.0", "--port", "8642"]
