FROM python:3.12-slim

# uv fijado a la misma versión usada en desarrollo.
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencias primero (capa cacheable). --frozen: instala exactamente uv.lock; --no-dev: sin pytest/ruff.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY app/ ./app/

RUN useradd --system --no-create-home appuser
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health/', timeout=3)"]

# main.run() valida la configuración (Settings) y levanta uvicorn con PORT y LOG_LEVEL.
CMD ["python", "main.py"]
