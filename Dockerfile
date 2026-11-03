FROM node:22-bookworm-slim AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    FIN_GRAPH_ALLOW_INSECURE_LEGACY_API=true FIN_GRAPH_DATA_DIR=/data
WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY --from=web-build /app/src/fin_graph/server/static ./src/fin_graph/server/static
RUN uv sync --frozen --no-dev --no-editable --extra app

RUN useradd --system --create-home fingraph
USER fingraph
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "fin-graph", "app", "--host", "0.0.0.0", "--data-dir", "/data"]
