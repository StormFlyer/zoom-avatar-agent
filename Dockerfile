FROM python:3.12-slim

# tini is PID 1, so processes the worker leaves behind are cleaned up and
# `docker stop` reaches the panel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    BEHIND_PROXY=1 \
    AGENT_MODE=start

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY docker-entrypoint.sh ./

# Everything the app writes lives in /data (a volume). The app expects these
# folders next to src/, so point them at the volume.
RUN useradd --create-home --uid 1000 app \
    && mkdir /data \
    && chown app:app /data /app \
    && for p in avatars knowledge meetings brand logs .fastembed_cache presets.json; do \
         ln -s "/data/$p" "/app/$p"; \
       done
USER 1000
VOLUME /data

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"]

ENTRYPOINT ["tini", "--", "./docker-entrypoint.sh"]
CMD ["python", "src/webui.py"]
