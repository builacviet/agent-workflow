FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# gh is required by the existing exact-SHA GitHub verification service.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gh \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
RUN python -m pip install --no-cache-dir ".[mcp]" \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /state \
    && chown appuser:appuser /state

ENV HOME=/home/appuser
USER appuser
EXPOSE 8000
CMD ["python", "-m", "my_wfc.server"]
