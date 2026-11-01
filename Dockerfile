FROM node:22-bookworm-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json ./
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app \
    GROK_REGISTER_CONFIG_FILE=/app/config/config.json \
    GROK_REGISTER_DATA_DIR=/app/data \
    GROK_REGISTER_OUTPUT_DIR=/app/data/outputs \
    CHROME_PATH=/usr/bin/chromium \
    GROK_REGISTER_CHROMIUM_HEADLESS=1 \
    GROK_REGISTER_CHROMIUM_NO_SANDBOX=1 \
    GROK_REGISTER_CHROMIUM_DISABLE_DEV_SHM_USAGE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-web.txt requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements-web.txt

COPY . .
COPY --from=frontend-build /frontend/dist /app/frontend/dist

RUN groupadd --system --gid 10001 grok \
    && useradd --system --uid 10001 --gid grok --create-home --home-dir /home/app grok \
    && mkdir -p /app/config /app/data /app/data/outputs \
    && chown -R grok:grok /app /home/app \
    && chmod 755 /app/docker/entrypoint.sh

EXPOSE 8092

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8092/health', timeout=3)" || exit 1

USER grok

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8092"]
