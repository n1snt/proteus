FROM node:22-bookworm-slim AS frontend-build

WORKDIR /web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PROTEUS_STATIC_DIR=/app/static

COPY --from=ghcr.io/astral-sh/uv:0.6.17 /uv /uvx /bin/

WORKDIR /app

RUN groupadd --system --gid 10001 proteus \
    && useradd --system --uid 10001 --gid proteus --create-home proteus

COPY pyproject.toml uv.lock ./
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev

COPY --from=frontend-build /web/dist/ /app/static/
COPY fixtures/ /app/fixtures/
COPY deploy/entrypoint.sh /usr/local/bin/proteus-entrypoint

RUN chmod 755 /usr/local/bin/proteus-entrypoint \
    && chown -R proteus:proteus /app

USER proteus

EXPOSE 8000

ENTRYPOINT ["proteus-entrypoint"]

CMD ["uvicorn", "proteus.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,172.28.0.0/16"]
