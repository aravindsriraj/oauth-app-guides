FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
ENV PATH="/srv/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
