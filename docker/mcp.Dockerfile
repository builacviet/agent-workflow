FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# MCP is local workflow storage only; no Git/GitHub client or App key.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src/agent_workflow /app/src/agent_workflow
COPY src/my_wfc /app/src/my_wfc
COPY src/order_service /app/src/order_service
RUN python -m pip install --no-cache-dir ".[mcp]" \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /state \
    && chown appuser:appuser /state

ENV HOME=/home/appuser
USER appuser
EXPOSE 8000
CMD ["python", "-m", "agent_workflow.server"]
