FROM node:22-bookworm-slim AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY --from=web-build /app/src/sre_graph/server/static ./src/sre_graph/server/static
RUN uv sync --frozen --no-dev --no-editable

RUN useradd --system --create-home sregraph
USER sregraph
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "sre_graph.server.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
